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
        jpeg=m.snapshot('127.0.0.1',port,'1234')
    assert jpeg.startswith(b'\xff\xd8')
    with av.open(io.BytesIO(jpeg)) as c:
        image=next(c.decode(video=0));assert (image.width,image.height)==(352,288)


def test_proxy_rejects_bad_token_before_monitor_connection():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'1234');proxy.start()
    import urllib.request,urllib.error
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:urllib.request.urlopen(f'http://127.0.0.1:{proxy.port}/wrong/stream.ts')
        assert caught.value.code==404
    finally:proxy.stop()


def test_proxy_listens_only_on_loopback_and_hides_password():
    m=media();proxy=m.VideoProxy('192.168.50.10',5801,'secret-test-value');proxy.start()
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
        with pytest.raises(m.ProtocolError,match='imagem'):m.snapshot('127.0.0.1',port,'1234')
    assert time.monotonic()-start<1


def test_proxy_stop_closes_session_during_handshake():
    import threading,concurrent.futures,urllib.request
    m=media();ready=threading.Event()
    def silent(listener):
        with listener.accept()[0] as control,listener.accept()[0] as video:
            control.settimeout(3);read(control);ready.set();assert control.recv(1)==b''
    with panel(silent) as port:
        proxy=m.VideoProxy('127.0.0.1',port,'1234');proxy.start()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future=pool.submit(urllib.request.urlopen,proxy.url)
            assert ready.wait(timeout=2)
            proxy.stop()
            assert not proxy._sessions
            try:
                response=future.result(timeout=2)
                try:assert response.read()==b''
                finally:response.close()
            except (OSError,ValueError):pass


def test_opening_exclusive_lease_closes_video_and_rejects_new_sessions():
    from contextlib import nullcontext
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture')
    first=proxy._new_session()
    # A request finishes and unregisters its session after close wakes it.
    import threading,time
    done=threading.Event()
    def finish():
        while not first.stopped.wait(.01):pass
        with proxy._session_lock:proxy._sessions.discard(first)
        done.set()
    worker=threading.Thread(target=finish,daemon=True);worker.start()
    with proxy.exclusive():
        assert first.stopped.is_set()
        with pytest.raises(m.ProtocolError):proxy._new_session()
    worker.join(1);assert done.is_set()
    again=proxy._new_session();again.close()


def test_automatic_thumbnail_never_opens_monitor(monkeypatch):
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture')
    def forbidden(*args,**kwargs):pytest.fail('Automatic thumbnail opened a monitor connection')
    monkeypatch.setattr(m.socket,'create_connection',forbidden)
    for _ in range(30):assert proxy.snapshot() is None
    proxy._last_image=b'cached-image'
    for _ in range(30):assert proxy.snapshot()==b'cached-image'


def test_only_one_preview_until_previous_session_closes(monkeypatch):
    m=media();now=[100.0];monkeypatch.setattr(m.time,'monotonic',lambda:now[0])
    proxy=m.VideoProxy('127.0.0.1',1,'fixture');first=proxy._new_session()
    with pytest.raises(m.ProtocolError):proxy._new_session()
    first.close()
    with proxy._session_lock:proxy._sessions.discard(first)
    second=proxy._new_session();second.close()


def test_preview_session_has_hard_deadline(monkeypatch):
    m=media();now=[100.0];monkeypatch.setattr(m.time,'monotonic',lambda:now[0])
    preview=m.Preview('127.0.0.1',1,'fixture')
    now[0]+=m.MAX_PREVIEW_SECONDS+1
    assert list(preview.frames(deadline=10000))==[]


def test_live_video_can_supply_cached_thumbnail():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture')
    proxy._cache_frame(av.VideoFrame(352,288,'yuv420p'))
    assert proxy.snapshot().startswith(b'\xff\xd8')


def test_live_h264_decoding_populates_thumbnail_without_network():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture')
    preview=proxy._new_session()
    proxy._cache_packet(synthetic_h264())
    assert proxy.snapshot().startswith(b'\xff\xd8')
    preview.close()


def test_proxy_delivers_low_bitrate_video_promptly(monkeypatch):
    import time,threading,urllib.request
    m=media();nal=synthetic_h264()
    class Replay:
        def __init__(self,*args):self.stopped=threading.Event()
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):self.stopped.set()
        def frames(self):
            until=time.monotonic()+8
            while not self.stopped.is_set() and time.monotonic()<until:
                yield nal
                self.stopped.wait(.3)
    monkeypatch.setattr(m,'Preview',Replay)
    proxy=m.VideoProxy('127.0.0.1',1,'fixture');proxy.start();started=time.monotonic()
    try:
        with urllib.request.urlopen(proxy.url,timeout=2) as response:
            data=response.read(188)
        assert data[0]==0x47
        assert time.monotonic()-started<2
    finally:proxy.stop()


def test_two_homekit_readers_share_one_monitor_session(monkeypatch):
    import threading,time,urllib.request,concurrent.futures
    m=media();nal=synthetic_h264();opened=[]
    class Replay:
        def __init__(self,*args):self.stopped=threading.Event();opened.append(self)
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):self.stopped.set()
        def frames(self):
            while not self.stopped.is_set():yield nal;self.stopped.wait(.05)
    monkeypatch.setattr(m,'Preview',Replay)
    proxy=m.VideoProxy('127.0.0.1',1,'fixture');proxy.start()
    try:
        with urllib.request.urlopen(proxy.url,timeout=3) as first:
            assert first.read(188)[0]==0x47
            with urllib.request.urlopen(proxy.url,timeout=3) as second:
                assert second.read(188)[0]==0x47
                assert len(opened)==1
    finally:proxy.stop()


def test_clean_video_close_does_not_impose_one_minute_delay(monkeypatch):
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture')
    first=proxy._new_session();first.close()
    with proxy._session_lock:proxy._sessions.discard(first)
    second=proxy._new_session();second.close()


def test_preview_closes_even_if_consumer_never_reads_frames(monkeypatch):
    import time
    m=media();monkeypatch.setattr(m,'MAX_PREVIEW_SECONDS',.1)
    class Conn:
        closed=False
        def shutdown(self,*args):pass
        def close(self):self.closed=True
    p=m.Preview('127.0.0.1',1,'fixture');p.control=Conn();p.video=Conn()
    time.sleep(.2)
    assert p.control.closed and p.video.closed
    p.close()


def test_closing_last_reader_releases_monitor_without_waiting_for_limit(monkeypatch):
    import threading,time,urllib.request
    m=media();nal=synthetic_h264();closed=threading.Event()
    class Replay:
        def __init__(self,*args):self.stopped=threading.Event()
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):self.stopped.set();closed.set()
        def frames(self):
            while not self.stopped.is_set():yield nal;self.stopped.wait(.05)
    monkeypatch.setattr(m,'Preview',Replay)
    proxy=m.VideoProxy('127.0.0.1',1,'fixture');proxy.start()
    try:
        with urllib.request.urlopen(proxy.url,timeout=3) as response:assert response.read(188)[0]==0x47
        assert closed.wait(5),'Last reader left but monitor session remained open'
    finally:proxy.stop()


def test_oversized_replay_waits_for_next_keyframe_instead_of_replaying_p_frames():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture');preview=proxy._new_session()
    hub=m._VideoHub(proxy,preview);hub.replay_valid=True
    hub.write(b'I'*(256*1024+1));hub.publish()
    assert not hub.replay_valid and not hub.replay
    hub.write(b'P'*188);hub.publish()
    reader=hub.attach()
    assert reader in hub.waiting and reader.empty()
    # The next keyframe boundary enables the new reader before its bytes.
    hub.write(b'next-keyframe');hub.publish(keyframe=True)
    assert hub.read(reader)==b'next-keyframe'
    preview.close()


def test_late_reader_receives_keyframe_being_prepared():
    m=media();proxy=m.VideoProxy('127.0.0.1',1,'fixture');preview=proxy._new_session()
    hub=m._VideoHub(proxy,preview)
    # Muxing/thumbnail work may happen before the frame is published.
    hub.write(b'current-IDR')
    reader=hub.attach()
    assert reader in hub.waiting
    hub.publish(keyframe=True)
    assert hub.read(reader)==b'current-IDR'
    preview.close()


def test_two_successive_homekit_readers_decode_images_from_same_upstream(monkeypatch):
    import shutil,subprocess,threading
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg not installed')
    m=media();nal=synthetic_h264();opened=[]
    class Replay:
        def __init__(self,*args):self.stopped=threading.Event();opened.append(self)
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):self.stopped.set()
        def frames(self):
            while not self.stopped.is_set():yield nal;self.stopped.wait(.15)
    monkeypatch.setattr(m,'Preview',Replay)
    proxy=m.VideoProxy('127.0.0.1',1,'fixture');proxy.start()
    try:
        for _ in range(2):
            result=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-i',proxy.url,'-frames:v','1','-f','image2pipe','-vcodec','mjpeg','pipe:1'],capture_output=True,timeout=10)
            assert result.returncode==0
            assert result.stdout.startswith(b'\xff\xd8')
        assert len(opened)==1
    finally:proxy.stop()
