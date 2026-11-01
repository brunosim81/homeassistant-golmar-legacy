"""On-demand video-only client and private loopback MPEG-TS adapter.

No alarm subscription, audio transmission or door command is implemented here.
"""
from __future__ import annotations

import hmac
import http.server
import io
import secrets
import select
import socket
import struct
import threading
import time
from fractions import Fraction

import av

from .protocol import ProtocolError, _expect, _header, _login, _read, derive_password

SNAPSHOT_TIMEOUT = 10.0


class Preview:
    """A bounded video session; sockets are closed even on decoder errors."""
    def __init__(self, host: str, port: int, password: str):
        self.host=host;self.port=port;self._password=password
        self.control=None;self.video=None;self.stopped=threading.Event()

    def __enter__(self):
        try:
            if self.stopped.is_set():raise ProtocolError('Vídeo terminado')
            self.control=socket.create_connection((self.host,self.port),5)
            if self.stopped.is_set():raise ProtocolError('Vídeo terminado')
            self.video=socket.create_connection((self.host,self.port),5)
            if self.stopped.is_set():raise ProtocolError('Vídeo terminado')
            login,_=_login(self.control,derive_password(self._password))
            self.control.sendall(_header(1,b'0'*32));_expect(_read(self.control),1,52)
            self.video.sendall(_header(0x221,login[316:320]+struct.pack('<HHII',0,1,1,0)))
            _expect(_read(self.video),0x221)
            return self
        except BaseException:
            self.close();raise

    def frames(self,deadline=None):
        limit=deadline if deadline is not None else time.monotonic()+600;heartbeat=time.monotonic()+5
        while not self.stopped.is_set() and time.monotonic()<limit:
            if time.monotonic()>=heartbeat:
                self.control.sendall(_header(1,b'0'*32));heartbeat=time.monotonic()+5
            try:
                ready,_,_=select.select([self.control,self.video],[],[],min(.25,max(0,limit-time.monotonic())))
            except (OSError,ValueError) as err:
                if self.stopped.is_set():break
                raise ProtocolError('Ligação de vídeo terminada') from err
            for connection in ready:
                p=_read(connection,maximum=2*1024*1024,deadline=min(limit,time.monotonic()+5))
                kind=struct.unpack_from('<H',p,8)[0]
                if connection is self.control:
                    if kind==1 and p[10]&3==0 and len(p)==52:
                        response=bytearray(p);response[10]=(response[10]&0xfc)|1;self.control.sendall(response)
                elif kind==0xe01 and len(p)>36:
                    data=p[32:]
                    if data.startswith((b'\0\0\0\1',b'\0\0\1')):
                        yield data

    def close(self):
        self.stopped.set()
        for connection in (self.control,self.video):
            if connection:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                connection.close()

    def __exit__(self,*args):self.close()


def _snapshot(preview,deadline):
    codec=av.CodecContext.create('h264','r')
    for data in preview.frames(deadline=deadline):
        for packet in codec.parse(data):
            for frame in codec.decode(packet):
                image=io.BytesIO();frame.to_image().save(image,format='JPEG');return image.getvalue()
    raise ProtocolError('Não foi recebida uma imagem H.264 válida')


def snapshot(host: str,port: int,password: str) -> bytes:
    """Read and decode a real keyframe; no audio output or device command."""
    deadline=time.monotonic()+SNAPSHOT_TIMEOUT
    with Preview(host,port,password) as preview:
        return _snapshot(preview,deadline)


class _PreviewReader:
    def __init__(self,preview):self.iterator=iter(preview.frames());self.pending=b''
    def read(self,size):
        if not self.pending:
            try:self.pending=next(self.iterator)
            except StopIteration:return b''
        data,self.pending=self.pending[:size],self.pending[size:]
        return data
    def readable(self):return True


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads=True
    block_on_close=False


class VideoProxy:
    """Source is reachable only on loopback and requires an unpredictable token."""
    def __init__(self,host,port,password):
        self._host=host;self._monitor_port=port;self._password=password
        self._token=secrets.token_urlsafe(32);self.server=None;self.port=None
        self._thread=None;self._sessions=set();self._session_lock=threading.Lock()
        self._slots=threading.BoundedSemaphore(2)
        self._stopping=threading.Event();self._clients=set()

    def start(self):
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()
            def do_GET(self):
                expected=f'/{owner._token}/stream.ts'
                if not hmac.compare_digest(self.path,expected):self.send_error(404);return
                if not owner._slots.acquire(blocking=False):self.send_error(503);return
                try:
                    preview=owner._new_session()
                    with owner._session_lock:owner._clients.add(self.connection)
                    with preview:
                        try:
                            source=av.open(_PreviewReader(preview),format='h264',buffer_size=4096,options={'probesize':'4096','analyzeduration':'0'})
                            with source:
                                self.send_response(200);self.send_header('Content-Type','video/MP2T');self.send_header('Cache-Control','no-store');self.end_headers()
                                with av.open(self.wfile,'w',format='mpegts',options={'mpegts_flags':'resend_headers','flush_packets':'1'}) as output:
                                    stream=output.add_stream_from_template(source.streams.video[0])
                                    count=0
                                    for packet in source.demux(video=0):
                                        if not packet.size:continue
                                        packet.stream=stream;packet.time_base=Fraction(1,90000)
                                        packet.pts=packet.dts=count*4500;packet.duration=4500
                                        output.mux(packet);count+=1
                        finally:
                            with owner._session_lock:owner._sessions.discard(preview)
                except (OSError,ProtocolError,av.FFmpegError):
                    self.close_connection=True
                finally:
                    with owner._session_lock:
                        owner._clients.discard(self.connection)
                        if 'preview' in locals():owner._sessions.discard(preview)
                    owner._slots.release()
            def log_message(self,*args):pass
        self.server=_Server(('127.0.0.1',0),Handler);self.port=self.server.server_port
        self._thread=threading.Thread(target=self.server.serve_forever,name='golmar-video-loopback',daemon=True);self._thread.start()

    @property
    def url(self):return f'http://127.0.0.1:{self.port}/{self._token}/stream.ts'

    def _new_session(self):
        with self._session_lock:
            if self._stopping.is_set():raise ProtocolError('Vídeo terminado')
            preview=Preview(self._host,self._monitor_port,self._password)
            self._sessions.add(preview)
            return preview

    def snapshot(self):
        if not self._slots.acquire(blocking=False):raise ProtocolError('Câmara ocupada')
        preview=None
        try:
            preview=self._new_session()
            deadline=time.monotonic()+SNAPSHOT_TIMEOUT
            with preview:return _snapshot(preview,deadline)
        finally:
            if preview:
                with self._session_lock:self._sessions.discard(preview)
            self._slots.release()

    def stop(self):
        with self._session_lock:
            self._stopping.set();sessions=list(self._sessions);clients=list(self._clients)
        for session in sessions:session.close()
        for client in clients:
            try:client.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            client.close()
        if self.server:self.server.shutdown();self.server.server_close()
        if self._thread:self._thread.join(timeout=2)
        with self._session_lock:self._sessions.clear();self._clients.clear()
