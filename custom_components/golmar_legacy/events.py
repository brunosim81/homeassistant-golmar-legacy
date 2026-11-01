"""Receive spontaneous local bell events; never subscribe, play video or open a door."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import logging
import select
import socket
import struct
import threading
import time

from .protocol import AuthenticationError, MAGIC, ProtocolError, _expect, _header, _login, _read, derive_password

_LOGGER = logging.getLogger(__name__)


def decode_ring(frame: bytes) -> dict | None:
    """Only the variant/channel/type physically captured on ART 7W V03."""
    if len(frame) < 76 or frame[:4] != MAGIC:
        return None
    if struct.unpack_from('<I', frame, 4)[0] != len(frame):
        return None
    if struct.unpack_from('<H', frame, 8)[0] != 0x505 or frame[11] != 0:
        return None
    if struct.unpack_from('<I', frame, 52)[0] != 0 or struct.unpack_from('<I', frame, 68)[0] != 7:
        return None
    return {'simulated': False, 'source': 'local_monitor', 'channel': 0, 'alarm_type': 7}


class RingDeduplicator:
    def __init__(self):
        self.recent: OrderedDict[bytes, float] = OrderedDict()

    def accept(self, frame: bytes, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        # Ignore transaction/header changes on retransmissions; never log UID/text.
        key = hashlib.sha256(frame[20:72]).digest()
        for old, when in list(self.recent.items()):
            if now - when >= 60:
                del self.recent[old]
        if key in self.recent:
            return False
        self.recent[key] = now
        while len(self.recent) > 64:
            self.recent.popitem(last=False)
        return True


class _EventSocket:
    """Enforce the exact read-only wire vocabulary validated on the monitor."""
    def __init__(self,connection):self.connection=connection
    def __getattr__(self,name):return getattr(self.connection,name)
    def sendall(self,data):
        if (len(data)<20 or data[:4]!=MAGIC
            or struct.unpack_from('<I',data,4)[0]!=len(data)
            or struct.unpack_from('<H',data,8)[0] not in (0x101,0x105,1)):
            raise ProtocolError('Comando não permitido na ligação de eventos')
        self.connection.sendall(data)


class BellListener:
    def __init__(self, host, port, password, callback):
        self.host, self.port = host, port
        self._password, self._callback = password, callback
        self._stop = threading.Event()
        self._thread = None
        self._connection = None
        self._dedup = RingDeduplicator()
        self.status = 'stopped'

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='golmar-bell-events', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Closing a socket is the only stop operation: no UNGUARD packet.
        conn = self._connection
        if conn:
            try:conn.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        if self._thread:
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                raise ProtocolError('A escuta Golmar ainda está a terminar; operação cancelada')
        self.status = 'stopped'

    def _run(self):
        delay = 5
        while not self._stop.is_set():
            try:
                self._session()
            except AuthenticationError:
                self.status = 'authentication_failed'
                _LOGGER.warning('Golmar bell authentication refused; automatic retry stopped')
                return
            except (OSError, ProtocolError, ValueError):
                self.status = 'reconnecting'
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 60)
        self.status = 'stopped'

    def _session(self):
        with socket.create_connection((self.host, self.port), 3) as raw:
            conn=_EventSocket(raw)
            self._connection = raw
            conn.settimeout(5)
            self.status = 'connecting'
            if self._stop.is_set():return
            login, _ = _login(conn, derive_password(self._password))
            if self._stop.is_set():
                return
            conn.sendall(_header(1, b'0' * 32))
            _expect(_read(conn), 1, 52)
            self.status = 'listening_without_subscription'
            self._receive(conn)

    def _receive(self, conn):
        buffer = bytearray()
        heartbeat = time.monotonic() + 5
        last_rx = time.monotonic()
        partial_since = None
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_rx > 120 or (partial_since is not None and now - partial_since > 5):
                raise ProtocolError('Tempo de escuta Golmar excedido')
            if now >= heartbeat:
                conn.sendall(_header(1, b'0' * 32))
                heartbeat = now + 5
            if not select.select([conn], [], [], .25)[0]:
                continue
            chunk = conn.recv(65536)
            if not chunk:
                raise ProtocolError('Ligação de eventos terminada')
            last_rx = time.monotonic()
            if not buffer:
                partial_since = last_rx
            buffer.extend(chunk)
            while len(buffer) >= 20:
                size = struct.unpack_from('<I', buffer, 4)[0]
                if buffer[:4] != MAGIC or not 20 <= size <= 65536:
                    raise ProtocolError('Cabeçalho de evento inválido')
                if len(buffer) < size:
                    break
                frame = bytes(buffer[:size])
                del buffer[:size]
                partial_since = time.monotonic() if buffer else None
                kind = struct.unpack_from('<H', frame, 8)[0]
                if kind == 1 and len(frame) == 52 and frame[10] & 3 == 0:
                    response = bytearray(frame)
                    response[10] = (response[10] & 0xfc) | 1
                    conn.sendall(response)
                ring = decode_ring(frame)
                if ring and self._dedup.accept(frame):
                    self._callback(ring)
