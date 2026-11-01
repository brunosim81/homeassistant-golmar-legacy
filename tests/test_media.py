"""No physical device used. Captured fixture is generated locally by an encoder."""
import importlib,sys,io
from pathlib import Path
from types import SimpleNamespace
import pytest
import av
from test_protocol import panel,handshake,read,packet

ROOT=Path(__file__).parents[1];sys.path.insert(0,str(ROOT))

def media():
    assert (ROOT/'custom_components/golmar_legacy/media.py').exists(),'Missing local media adapter'
    return importlib.import_module('custom_components.golmar_legacy.media')


def synthetic_h264():
    output=io.BytesIO()
    with av.open(output,'w',format='h264') as c:
        stream=c.add_stream('libx264',rate=20);stream.width=352;stream.height=288;stream.pix_fmt='yuv420p';stream.options={'tune':'zerolatency','preset':'ultrafast'}
        for _ in range(3):
            frame=av.VideoFrame(352,288,'yuv420p')
            for plane in frame.planes:plane.update(bytes(plane.buffer_size))
            for p in stream.encode(frame):c.mux(p)
        for p in stream.encode():c.mux(p)
    return output.getvalue()


def serve_preview(listener,nal):
    with listener.accept()[0] as control,listener.accept()[0] as video:
        control.settimeout(3);video.settimeout(3);handshake(control)
        p=read(control);assert p[8:10]==b'\x01\0';control.sendall(packet(1,b'0'*32))
        p=read(video);assert p[8:10]==b'!\x02';assert p[20:24]==b'\x03\0\xab\xcd'
        video.sendall(packet(0x221));video.sendall(packet(0xe01,bytes(12)+nal))
        # No alarm configuration, talk, opening or arbitrary command is sent.
        assert control.recv(1)==b''


def test_snapshot_decodes_video_without_opening_or_alarm_commands():
    m=media();nal=synthetic_h264()
    with panel(lambda s:serve_preview(s,nal)) as port:
        jpeg=m.snapshot('127.0.0.1',port,'2108')
    assert jpeg.startswith(b'\xff\xd8')
    with av.open(io.BytesIO(jpeg)) as c:
        image=next(c.decode(video=0));assert (image.width,image.height)==(352,288)


def test_proxy_rejects_bad_token_before_monitor_connection():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'2108');proxy.start()
    import urllib.request,urllib.error
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:urllib.request.urlopen(f'http://127.0.0.1:{proxy.port}/wrong/stream.ts')
        assert caught.value.code==404
    finally:proxy.stop()


def test_proxy_listens_only_on_loopback_and_hides_password():
    m=media();proxy=m.VideoProxy('192.168.50.20',5801,'secret-test-value');proxy.start()
    try:
        assert proxy.server.server_address[0]=='127.0.0.1'
        assert 'secret-test-value' not in repr(proxy)
        assert proxy.url.startswith('http://127.0.0.1:')
    finally:proxy.stop()


def test_silent_video_respects_snapshot_deadline(monkeypatch):
    import time
    m=media();monkeypatch.setattr(m,'SNAPSHOT_TIMEOUT',.15)
    def silent(listener):
        with listener.accept()[0] as control,listener.accept()[0] as video:
            control.settimeout(3);handshake(control);read(control);control.sendall(packet(1,b'0'*32));read(video);video.sendall(packet(0x221));assert control.recv(1)==b''
    start=time.monotonic()
    with panel(silent) as port:
        with pytest.raises(m.ProtocolError,match='imagem'):m.snapshot('127.0.0.1',port,'2108')
    assert time.monotonic()-start<1


def test_proxy_stop_closes_session_during_handshake():
    import threading,concurrent.futures,urllib.request
    m=media();ready=threading.Event()
    def silent(listener):
        with listener.accept()[0] as control,listener.accept()[0] as video:
            control.settimeout(3);read(control);ready.set();assert control.recv(1)==b''
    with panel(silent) as port:
        proxy=m.VideoProxy('127.0.0.1',port,'2108');proxy.start()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future=pool.submit(urllib.request.urlopen,proxy.url)
            assert ready.wait(timeout=2)
            proxy.stop()
            assert not proxy._sessions
            with pytest.raises(Exception):future.result(timeout=2)
