"""Golmar G2Call legacy UMEye TCP client, with no background commands.

Wire format adapted from jdntortosa/fermax-wayfi-ha (MIT, see LICENSE).
G0053 and password derivation verified against G2Call iOS 5.3.3.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import time

MAGIC = bytes.fromhex('eeeeffff')
HELLO = bytes.fromhex('eeeeffff700000000101000000000000000000000101020103010000000000000000000000000000000000000300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000')

TIMEOUT = 5.0


class ProtocolError(Exception):
    """Unsupported or invalid response; contains no credential material."""


class AuthenticationError(ProtocolError):
    """The monitor rejected the login."""


def derive_password(password: str) -> str:
    """Derive the 8-byte UMEye password, not the separate unlock PIN."""
    raw = password.encode('utf-8')
    if not raw or len(raw) > 32 or b'\0' in raw:
        raise ValueError('A contra-senha deve ter entre 1 e 32 bytes e não pode conter NUL.')
    digest = hashlib.md5(raw).digest()
    alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    return ''.join(alphabet[(digest[i]+digest[i+1]) % 62] for i in range(0,16,2))


def _header(kind: int, body: bytes, nonce: int = 0) -> bytes:
    return struct.pack('<4sIHBBII',MAGIC,20+len(body),kind,0,0,nonce,0)+body


def _read(sock: socket.socket, maximum: int = 4096, deadline: float | None = None) -> bytes:
    """Reassemble one bounded frame with a total deadline."""
    deadline = deadline if deadline is not None else time.monotonic()+TIMEOUT
    def exact(size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Tempo de resposta excedido')
            sock.settimeout(remaining)
            block = sock.recv(size-len(result))
            if not block:
                raise ProtocolError('O monitor terminou a ligação antes da resposta completa')
            result.extend(block)
        return bytes(result)
    head = exact(20)
    size = struct.unpack_from('<I',head,4)[0]
    if head[:4] != MAGIC or not 20 <= size <= maximum:
        raise ProtocolError('Cabeçalho ou tamanho de resposta inválido')
    return head+exact(size-20)


def _expect(frame: bytes, kind: int, minimum: int = 20) -> None:
    if len(frame) < minimum or struct.unpack_from('<H',frame,8)[0] != kind or frame[10] != 1 or frame[11] != 0:
        raise ProtocolError('O monitor rejeitou o pedido ou devolveu uma resposta inesperada')


def _login(sock: socket.socket, secret: str) -> tuple[bytes, dict[str, str | int]]:
    sock.sendall(HELLO)
    hello = _read(sock)
    _expect(hello,0x101,112)
    version = struct.unpack_from('<H',hello,46)[0]
    if len(hello) != 112 or version != 0x213 or hello[108] != 1:
        raise ProtocolError('Este modo de autenticação legacy ainda não foi validado')
    try:
        challenge = hello[48:84].rstrip(b'\0')
        if not challenge:
            raise ValueError
        base64.b64decode(challenge,validate=True)
        auth = hashlib.md5(challenge+b':'+secret.encode('ascii')).hexdigest().encode('ascii')
    except (ValueError,UnicodeError) as err:
        raise ProtocolError('Desafio de autenticação inválido') from err
    body = bytearray(541)
    body[:32] = b'admin'.ljust(32,b'\0')
    body[32:64] = auth
    struct.pack_into('<II',body,288,1,5)
    body[-5:] = b'G0053'
    sock.sendall(_header(0x105,body))
    response = _read(sock)
    if struct.unpack_from('<H',response,8)[0] == 0x105 and response[10] == 1 and response[11] in (4,5):
        raise AuthenticationError('Autenticação recusada pelo monitor')
    _expect(response,0x105,561)
    if len(response) != 561:
        raise ProtocolError('Comprimento de autenticação inesperado')
    return response, {'protocol_version':hex(version),'password_mode':hello[108]}


def test_connection(host: str, port: int, password: str) -> dict[str, str | int]:
    """HELLO and LOGIN only. Never video, placeholder, queries or door commands."""
    secret = derive_password(password)
    with socket.create_connection((host,port),TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        _, info = _login(sock,secret)
        return info


# Reference queries from the MIT Fermax implementation; no state-changing TLV.
_PRE_OPEN_QUERIES = (
    (2042, b'\0'),
    (1952, bytes(32)),
    (2048, b'\0'),
    (2040, b'\0'),
)


def open_door(host: str, port: int, password: str) -> None:
    """Explicit one-shot opening of panel 0 / lock 0. Never called at setup.

    Physical opening is unvalidated on ART 7W V03. No channel rotation,
    fallback or retry, especially after a possibly delivered command.
    """
    secret = derive_password(password)
    with socket.create_connection((host,port),TIMEOUT) as control:
        with socket.create_connection((host,port),TIMEOUT) as video:
            control.settimeout(TIMEOUT)
            video.settimeout(TIMEOUT)
            login, _ = _login(control,secret)
            control.sendall(_header(1,b'0'*32))
            _expect(_read(control),1,52)
            # Copy the assigned session id unchanged; do not guess other channels.
            body = login[316:320]+struct.pack('<HHII',0,1,1,0)
            video.sendall(_header(0x221,body))
            _expect(_read(video),0x221)
            deadline = time.monotonic()+8.0
            while True:
                frame = _read(video,maximum=2*1024*1024,deadline=deadline)
                if struct.unpack_from('<H',frame,8)[0] == 0xe01 and frame[11] == 0 and len(frame)>20:
                    break
            # Allow the bus to settle as in the reference client, draining media.
            margin = min(deadline,time.monotonic()+1.0)
            while time.monotonic()<margin:
                try:
                    _read(video,maximum=2*1024*1024,deadline=margin)
                except TimeoutError:
                    break
            for command, query in _PRE_OPEN_QUERIES:
                control.settimeout(TIMEOUT)
                control.sendall(_header(0xa01,struct.pack('<II',command,len(query))+query))
                try:
                    response = _read(control,deadline=time.monotonic()+1.5)
                except TimeoutError as err:
                    # A partial/late response could be confused with the unlock ACK.
                    # Fail before sending any unlock instead of attempting a retry.
                    raise ProtocolError('O monitor não confirmou os pedidos de preparação; não foi enviada abertura') from err
                _expect(response,0xa01,28)
                if struct.unpack_from('<I',response,20)[0] != command:
                    raise ProtocolError('Resposta de preparação inesperada; não foi enviada abertura')
            nonce = 0x12345678
            body = bytes(4)+secret.encode('ascii').ljust(15,b'\0')+bytes(17)
            control.settimeout(TIMEOUT)
            control.sendall(_header(0xa01,struct.pack('<II',2046,36)+body,nonce))
            try:
                response = _read(control)
                _expect(response,0xa01,29)
                if struct.unpack_from('<I',response,12)[0] != nonce or struct.unpack_from('<II',response,20) != (2046,1) or response[28] != 0:
                    raise ProtocolError('Confirmação de abertura inesperada')
            except (OSError,ProtocolError) as err:
                raise ProtocolError('Comando enviado, mas a abertura não foi confirmada. Verifique a porta antes de repetir.') from err
