"""Synthetic wire tests based on the physical ring type, never a real monitor."""
import importlib,sys,struct,threading,time
from pathlib import Path
import pytest
from test_protocol import panel,handshake,read,packet
sys.path.insert(0,str(Path(__file__).parents[1]))
def events():return importlib.import_module('custom_components.golmar_legacy.events')
def bell(kind=0x505,channel=0,alarm=7,stamp=b'123456789012'):
 body=bytearray(180);struct.pack_into('<I',body,32,channel);body[36:48]=stamp;struct.pack_into('<I',body,48,alarm)
 return packet(kind,body)
def test_captured_wire_ring_only_and_bounds():
 m=events();assert m.decode_ring(bell()) is not None
 for frame in (bell(alarm=2),bell(channel=1),bell(kind=0x504),packet(0x505,b'bad')):assert m.decode_ring(frame) is None
 bad=bytearray(bell());bad[0]=0;assert m.decode_ring(bytes(bad)) is None

def test_dedup_across_reconnect_and_bounded_memory():
 m=events();d=m.RingDeduplicator()
 assert d.accept(bell(),now=10)
 assert not d.accept(bell(),now=11)
 assert d.accept(bell(stamp=b'ABCDEFGHIJKL'),now=12)
 for n in range(200):d.accept(bell(stamp=n.to_bytes(12,'little')),now=15+n)
 assert len(d.recent)<=64

def test_authenticated_listener_receives_ring_without_guard_video_or_relay():
 m=events();got=[];received=threading.Event();wire_done=threading.Event()
 def callback(event):got.append(event);received.set()
 def serve(listener):
  with listener.accept()[0] as c:
   c.settimeout(4);handshake(c);assert read(c)[8:10]==b'\x01\0';c.sendall(packet(1,b'0'*32))
   data=bell(alarm=2)+bell()+bell()
   for i in range(0,len(data),7):c.sendall(data[i:i+7])
   wire_done.set()
   try:assert c.recv(1)==b'', 'No GUARD/UNGUARD/video/relay command may follow authentication'
   except ConnectionResetError:pass  # Immediate socket close may reset unread duplicate frames.
 with panel(serve) as port:
  client=m.BellListener('127.0.0.1',port,'1234',callback)
  client.start();assert received.wait(3);assert wire_done.wait(1);client.stop()
 assert len(got)==1 and got[0]['simulated'] is False and got[0]['alarm_type']==7
 assert not client.running


def test_listener_refused_authentication_does_not_retry():
 m=events()
 def serve(listener):
  with listener.accept()[0] as c:
   handshake(c,status=4);assert c.recv(1)==b''
 with panel(serve) as port:
  client=m.BellListener('127.0.0.1',port,'1234',lambda _:None);client.start()
  deadline=time.monotonic()+2
  while client.running and time.monotonic()<deadline:time.sleep(.01)
  assert client.status=='authentication_failed';assert not client.running;client.stop()


def test_stop_interrupts_silent_handshake_without_subscription():
 m=events();accepted=threading.Event()
 def serve(listener):
  with listener.accept()[0] as c:
   c.settimeout(2);read(c);accepted.set();assert c.recv(1)==b''
 with panel(serve) as port:
  client=m.BellListener('127.0.0.1',port,'1234',lambda _:None);client.start()
  assert accepted.wait(1);started=time.monotonic();client.stop()
  assert time.monotonic()-started<1;assert not client.running


def test_event_socket_blocks_guard_unguard_video_queries_and_unlock():
 m=events();sent=[]
 class Sink:
  def sendall(self,data):sent.append(data)
 wire=m._EventSocket(Sink())
 for kind in (0x501,0x502,0x221,0xa01,0x401,0x402,0x301):
  with pytest.raises(m.ProtocolError):wire.sendall(packet(kind,b'0'*32))
 assert sent==[]
 # Valid heartbeat; no other payload crosses the socket.
 wire.sendall(packet(1,b'0'*32));assert len(sent)==1
