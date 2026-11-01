"""Loopback tests; none of these tests contact a real intercom."""
import concurrent.futures
import hashlib
import socket
import struct
import threading
from contextlib import contextmanager
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'custom_components/golmar_legacy/protocol.py'
if MODULE.exists():
    spec = importlib.util.spec_from_file_location('golmar_protocol', MODULE)
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
else:
    protocol = None

MAGIC = b'\xee\xee\xff\xff'
CHALLENGE = b'dGVzdC1jaGFsbGVuZ2U='


def packet(kind, body=b'', status=0, nonce=0):
    return struct.pack('<4sIHBBII', MAGIC, 20+len(body), kind, 1, status, nonce, 0)+body


def read(sock):
    def exact(n):
        data = b''
        while len(data)<n:
            part = sock.recv(n-len(data))
            if not part: raise EOFError
            data += part
        return data
    header = exact(20)
    return header + exact(struct.unpack_from('<I', header, 4)[0]-20)


def handshake(conn, status=0):
    hello = read(conn)
    assert len(hello)==112 and hello[8:10]==b'\x01\x01'
    body=bytearray(92)
    struct.pack_into('<H',body,26,0x213)
    body[28:28+len(CHALLENGE)] = CHALLENGE
    body[88]=1
    response=packet(0x101,body)
    # Fragmented TCP responses must be reassembled.
    for start in range(0,len(response),7): conn.sendall(response[start:start+7])
    login=read(conn)
    assert len(login)==561 and login[20:52]==b'admin'.ljust(32,b'\0')
    assert login[52:84]==hashlib.md5(CHALLENGE+b':d2gNs1nj').hexdigest().encode()
    assert login[308:316]==b'\x01\0\0\0\x05\0\0\0'
    assert login[-5:]==b'G0053'
    body=bytearray(541);body[296:300]=b'\x03\0\xab\xcd'
    conn.sendall(packet(0x105,body,status=status))


@contextmanager
def panel(handler):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(2);listener.settimeout(3)
    executor=concurrent.futures.ThreadPoolExecutor(1)
    def run():
        with listener: handler(listener)
    future=executor.submit(run)
    try: yield listener.getsockname()[1]
    finally:
        try: future.result(timeout=5)
        finally: executor.shutdown(wait=True)


def test_client_exists():
    assert protocol is not None, 'Missing legacy protocol implementation'


def test_read_only_login_never_sends_placeholder_video_or_commands():
    assert protocol is not None
    def serve(listener):
        with listener.accept()[0] as conn:
            conn.settimeout(2);handshake(conn)
            assert conn.recv(1)==b''
    with panel(serve) as port:
        result=protocol.test_connection('127.0.0.1',port,'1234')
    assert result=={'protocol_version':'0x213','password_mode':1}


def test_wrong_password_is_not_retried():
    assert protocol is not None
    def serve(listener):
        with listener.accept()[0] as conn:
            handshake(conn,status=4)
            assert conn.recv(1)==b''
    with panel(serve) as port:
        with pytest.raises(protocol.AuthenticationError):
            protocol.test_connection('127.0.0.1',port,'1234')


@pytest.mark.parametrize('kind,body,status',[(0x999,bytes(92),0),(0x101,bytes(92),0),(0x101,b'',0),(0x101,bytes(92),4)])
def test_unsupported_hello_sends_no_login(kind,body,status):
    assert protocol is not None
    def serve(listener):
        with listener.accept()[0] as conn:
            read(conn);conn.sendall(packet(kind,body,status=status));assert conn.recv(1)==b''
    with panel(serve) as port:
        with pytest.raises(protocol.ProtocolError):protocol.test_connection('127.0.0.1',port,'1234')


def test_oversized_frame_fails_without_reading_body():
    assert protocol is not None
    def serve(listener):
        with listener.accept()[0] as conn:
            read(conn);conn.sendall(struct.pack('<4sIHBBII',MAGIC,0xffffffff,0x101,1,0,0,0));assert conn.recv(1)==b''
    with panel(serve) as port:
        with pytest.raises(protocol.ProtocolError):protocol.test_connection('127.0.0.1',port,'1234')


def opening_panel(listener, *, wake=True, ack_status=0, ack_command=2046):
    with listener.accept()[0] as control, listener.accept()[0] as video:
        control.settimeout(3);video.settimeout(3);handshake(control)
        placeholder=read(control)
        assert placeholder[8:10]==b'\x01\0' and placeholder[20:]==b'0'*32
        control.sendall(packet(1,b'0'*32))
        start=read(video)
        assert start[8:10]==b'!\x02'
        assert start[20:]==b'\x03\0\xab\xcd\0\0\x01\0\x01\0\0\0\0\0\0\0'
        video.sendall(packet(0x221,status=0 if wake else 6))
        if not wake:
            assert control.recv(1)==b''
            return
        video.sendall(packet(0xe01,b'test-frame'))
        # Only the four documented read-only queries may precede unlock.
        for command in (2042,1952,2048,2040):
            query=read(control)
            assert struct.unpack_from('<I',query,20)[0]==command
            control.sendall(packet(0xa01,query[20:28]+b'\0'))
        unlock=read(control)
        assert len(unlock)==64 and unlock[8:10]==b'\x01\x0a'
        assert struct.unpack_from('<II',unlock,20)==(2046,36)
        assert unlock[28:]==b'\0\0\0\0'+b'd2gNs1nj'.ljust(15,b'\0')+bytes(17)
        nonce=struct.unpack_from('<I',unlock,12)[0]
        control.sendall(packet(0xa01,struct.pack('<II',ack_command,1)+b'\0',status=ack_status,nonce=nonce))
        # Never retry an unlock, even when its acknowledgment was rejected.
        assert control.recv(1)==b''


def test_explicit_open_has_one_command_after_successful_wake():
    assert hasattr(protocol,'open_door')
    with panel(opening_panel) as port:protocol.open_door('127.0.0.1',port,'1234')


def test_failed_wake_sends_no_door_command():
    assert hasattr(protocol,'open_door')
    with panel(lambda s:opening_panel(s,wake=False)) as port:
        with pytest.raises(protocol.ProtocolError):protocol.open_door('127.0.0.1',port,'1234')


@pytest.mark.parametrize('status,command',[(4,2046),(0,123)])
def test_invalid_open_acknowledgment_is_not_reported_as_success(status,command):
    assert hasattr(protocol,'open_door')
    with panel(lambda s:opening_panel(s,ack_status=status,ack_command=command)) as port:
        with pytest.raises(protocol.ProtocolError):protocol.open_door('127.0.0.1',port,'1234')


def test_video_timeout_reports_pre_unlock_stage_and_sends_no_relay(monkeypatch):
    original=protocol._read;count=0
    def timeout_on_video(*args,**kwargs):
        nonlocal count
        count+=1
        if count==5:raise TimeoutError('synthetic timeout')
        return original(*args,**kwargs)
    monkeypatch.setattr(protocol,'_read',timeout_on_video)
    def serve(listener):
        with listener.accept()[0] as control,listener.accept()[0] as video:
            handshake(control);read(control);control.sendall(packet(1,b'0'*32))
            read(video);video.sendall(packet(0x221));assert control.recv(1)==b''
    with panel(serve) as port:
        with pytest.raises(protocol.ProtocolError,match='imagem.*não foi enviada abertura'):
            protocol.open_door('127.0.0.1',port,'1234')
