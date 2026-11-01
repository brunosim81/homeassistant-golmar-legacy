"""On-demand video-only client and private loopback MPEG-TS adapter.

No alarm subscription, audio transmission or door command is implemented here.
"""
from __future__ import annotations

import hmac
import http.server
import io
import queue
import secrets
import select
import socket
import struct
import threading
import time
from fractions import Fraction
from contextlib import contextmanager

import av

from .protocol import ProtocolError, _expect, _header, _login, _read, derive_password

SNAPSHOT_TIMEOUT = 10.0
MAX_PREVIEW_SECONDS = 45.0


class Preview:
    """A bounded video session; sockets are closed even on decoder errors."""
    def __init__(self, host: str, port: int, password: str):
        self.host=host;self.port=port;self._password=password
        self.control=None;self.video=None;self.stopped=threading.Event()
        self._deadline=time.monotonic()+MAX_PREVIEW_SECONDS
        self._expiry=threading.Timer(MAX_PREVIEW_SECONDS,self.close)
        self._expiry.daemon=True;self._expiry.start()

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
        limit=min(deadline,self._deadline) if deadline is not None else self._deadline;heartbeat=time.monotonic()+5
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
        self._expiry.cancel()
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


class _VideoHub:
    """One monitor session, with bounded queues for independent HomeKit readers."""
    def __init__(self,owner,preview):
        self.owner=owner;self.preview=preview;self.readers=set();self.replay=[];self.replay_size=0
        self.finished=False;self.idle_timer=None
        self.replay_valid=False;self.waiting=set();self.queued_bytes={};self.pending=bytearray()
        self.thread=threading.Thread(target=self.run,name='golmar-shared-video',daemon=True)

    def attach(self):
        if self.finished:raise ProtocolError('A sessão de vídeo terminou')
        if len(self.readers)>=4:raise ProtocolError('Demasiados leitores de vídeo')
        if self.idle_timer:self.idle_timer.cancel();self.idle_timer=None
        reader=queue.Queue(maxsize=128)
        if self.replay_valid and self.replay:reader.put_nowait(b''.join(self.replay))
        else:self.waiting.add(reader)
        self.queued_bytes[reader]=self.replay_size if self.replay_valid else 0
        self.readers.add(reader)
        return reader

    def detach(self,reader):
        with self.owner._session_lock:
            self.readers.discard(reader);self.waiting.discard(reader);self.queued_bytes.pop(reader,None)
            if not self.readers and not self.finished:
                self.idle_timer=threading.Timer(2,self.close_if_idle)
                self.idle_timer.daemon=True;self.idle_timer.start()

    def close_if_idle(self):
        with self.owner._session_lock:
            if self.readers:return
            self.finished=True
        self.preview.close()

    def write(self,data):
        # Group all TS fragments of a muxed frame before fan-out. A large
        # keyframe must not overflow queues merely because AVIO writes 188B.
        if len(self.pending)+len(data)>3*1024*1024:raise ProtocolError('Vídeo excedeu o limite de memória')
        self.pending.extend(data)
        return len(data)

    def publish(self,keyframe=False):
        data=bytes(self.pending);self.pending.clear()
        if not data:return
        with self.owner._session_lock:
            if keyframe:
                self.replay.clear();self.replay_size=0;self.replay_valid=True;self.waiting.clear()
            if self.replay_valid:
                self.replay.append(data);self.replay_size+=len(data)
                if self.replay_size>256*1024:
                    self.replay.clear();self.replay_size=0;self.replay_valid=False
            for reader in tuple(self.readers):
                if reader in self.waiting:continue
                try:
                    if self.queued_bytes[reader]+len(data)>1024*1024:raise queue.Full
                    reader.put_nowait(data);self.queued_bytes[reader]+=len(data)
                except queue.Full:
                    self.readers.discard(reader);self.queued_bytes.pop(reader,None)
                    while not reader.empty():
                        try:reader.get_nowait()
                        except queue.Empty:break
                    reader.put_nowait(None)

    def read(self,reader):
        data=reader.get(timeout=1)
        if data is not None:
            with self.owner._session_lock:
                if reader in self.queued_bytes:self.queued_bytes[reader]-=len(data)
        return data

    def writable(self):return True
    def flush(self):pass

    def run(self):
        try:
            with self.preview:
                with av.open(_PreviewReader(self.preview),format='h264',buffer_size=4096,options={'probesize':'4096','analyzeduration':'0'}) as source:
                    with av.open(self,'w',format='mpegts',buffer_size=188,options={'mpegts_flags':'resend_headers+pat_pmt_at_frames','flush_packets':'1'}) as output:
                        stream=output.add_stream_from_template(source.streams.video[0]);count=0
                        for packet in source.demux(video=0):
                            if self.finished or self.preview.stopped.is_set():break
                            if not packet.size:continue
                            self.owner._cache_packet(bytes(packet))
                            packet.stream=stream;packet.time_base=Fraction(1,90000)
                            packet.pts=packet.dts=count*4500;packet.duration=4500
                            output.mux(packet);self.publish(keyframe=packet.is_keyframe);count+=1
        except (OSError,ProtocolError,av.FFmpegError,ValueError):
            pass
        finally:
            self.preview.close()
            with self.owner._session_lock:
                self.finished=True
                if self.idle_timer:self.idle_timer.cancel()
                self.owner._sessions.discard(self.preview)
                for reader in self.readers:
                    while not reader.empty():
                        try:reader.get_nowait()
                        except queue.Empty:break
                    reader.put_nowait(None)
                self.readers.clear();self.waiting.clear();self.queued_bytes.clear()


class VideoProxy:
    """Source is reachable only on loopback and requires an unpredictable token."""
    def __init__(self,host,port,password):
        self._host=host;self._monitor_port=port;self._password=password
        self._token=secrets.token_urlsafe(32);self.server=None;self.port=None
        self._thread=None;self._sessions=set();self._session_lock=threading.RLock()
        self._hub=None
        self._last_started=float('-inf');self._last_image=None;self._image_time=float('-inf')
        self._stopping=threading.Event();self._clients=set()
        self._paused=False

    def start(self):
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()
            def do_GET(self):
                expected=f'/{owner._token}/stream.ts'
                if not hmac.compare_digest(self.path,expected):self.send_error(404);return
                hub=None;reader=None
                try:
                    with owner._session_lock:
                        if owner._stopping.is_set() or owner._paused:raise ProtocolError('Vídeo indisponível')
                        hub=owner._hub
                        if hub is None or hub.finished:
                            hub=_VideoHub(owner,owner._new_session());owner._hub=hub
                            reader=hub.attach();hub.thread.start()
                        else:reader=hub.attach()
                        owner._clients.add(self.connection)
                    self.send_response(200);self.send_header('Content-Type','video/MP2T');self.send_header('Cache-Control','no-store');self.end_headers()
                    while not owner._stopping.is_set():
                        try:data=hub.read(reader)
                        except queue.Empty:
                            if hub.finished:break
                            continue
                        if data is None:break
                        self.wfile.write(data)
                except ProtocolError:
                    if reader is None:self.send_error(503,'Video temporarily unavailable')
                    self.close_connection=True
                except OSError:
                    self.close_connection=True
                finally:
                    if hub is not None and reader is not None:hub.detach(reader)
                    with owner._session_lock:owner._clients.discard(self.connection)
            def log_message(self,*args):pass
        self.server=_Server(('127.0.0.1',0),Handler);self.port=self.server.server_port
        self._thread=threading.Thread(target=self.server.serve_forever,name='golmar-video-loopback',daemon=True);self._thread.start()

    @property
    def url(self):return f'http://127.0.0.1:{self.port}/{self._token}/stream.ts'

    def _new_session(self):
        with self._session_lock:
            if self._stopping.is_set() or self._paused:raise ProtocolError('Vídeo temporariamente indisponível')
            if self._sessions:raise ProtocolError('Já existe uma sessão de vídeo Golmar')
            self._thumbnail_codec=av.CodecContext.create('h264','r')
            preview=Preview(self._host,self._monitor_port,self._password)
            self._sessions.add(preview)
            return preview

    @contextmanager
    def exclusive(self):
        """Close this integration's previews before a single explicit operation."""
        with self._session_lock:
            self._paused=True
            sessions=list(self._sessions)
        try:
            for session in sessions:session.close()
            deadline=time.monotonic()+6
            while True:
                with self._session_lock:busy=bool(self._sessions)
                if not busy:break
                if time.monotonic()>=deadline:
                    raise ProtocolError('As sessões de vídeo ainda estão a terminar; abertura não enviada')
                time.sleep(.05)
            yield
        finally:
            with self._session_lock:self._paused=False

    def _cache_frame(self,frame):
        if time.monotonic()-self._image_time<5:return
        image=io.BytesIO();frame.to_image().save(image,format='JPEG')
        with self._session_lock:
            self._last_image=image.getvalue();self._image_time=time.monotonic()

    def _cache_packet(self,data):
        try:
            for packet in self._thumbnail_codec.parse(data):
                for frame in self._thumbnail_codec.decode(packet):self._cache_frame(frame)
        except av.FFmpegError:
            # A missing/corrupt keyframe must not interrupt the live video.
            pass

    def snapshot(self):
        """HomeKit background images must never wake or call the monitor."""
        with self._session_lock:return self._last_image

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
