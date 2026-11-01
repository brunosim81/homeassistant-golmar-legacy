"""Serialize explicit operations; never poll or queue door openings."""
from __future__ import annotations

import asyncio
import time
import threading
import av
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .media import VideoProxy, snapshot
from .protocol import ProtocolError, open_door, test_connection
from .events import BellListener


@dataclass
class GolmarRuntime:
    host: str
    port: int
    password: str = field(repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock,repr=False)
    info: dict[str, Any] = field(default_factory=dict)

    video_proxy: VideoProxy | None = field(default=None,repr=False)
    _snapshot_job: Any = field(default=None,repr=False)
    _snapshot_image: bytes | None = field(default=None,repr=False)
    _snapshot_time: float = 0
    _doorbell_listeners: list = field(default_factory=list,repr=False)
    _events: BellListener | None = field(default=None,repr=False)
    _closing: bool = False
    _hass: Any = field(default=None,repr=False)
    _event_lock: Any = field(default_factory=threading.Lock,repr=False)

    def start_events(self,hass):
        with self._event_lock:
            if self._closing:return
            self._hass=hass
            if self._events is None:
                self._events=BellListener(self.host,self.port,self.password,self._real_ring)
            self._events.start()

    def stop_events(self):
        with self._event_lock:
            self._closing=True
            if self._events:self._events.stop()

    def _real_ring(self,attributes):
        if not self._closing and self._hass:
            self._hass.loop.call_soon_threadsafe(self._deliver_real_ring,attributes)

    def _deliver_real_ring(self,attributes):
        if not self._closing:
            for listener in tuple(self._doorbell_listeners):listener(attributes)

    def start_media(self):
        self.video_proxy=VideoProxy(self.host,self.port,self.password)
        self.video_proxy.start()

    def stop_media(self):
        if self.video_proxy:self.video_proxy.stop()

    def stop_all(self):
        try:self.stop_events()
        finally:self.stop_media()

    def add_doorbell_listener(self,listener):
        self._doorbell_listeners.append(listener)
        def remove():
            if listener in self._doorbell_listeners:self._doorbell_listeners.remove(listener)
        return remove

    def simulate_doorbell(self):
        # No real subscription or device command is hidden behind this test.
        for listener in tuple(self._doorbell_listeners):
            listener({'simulated':True,'source':'manual_test','real_call_detection':'not_validated'})

    async def async_snapshot(self,hass):
        # A thumbnail request is automatic, not permission to start a call.
        return self.video_proxy.snapshot() if self.video_proxy else None

    async def _async_run(self, hass: HomeAssistant, operation):
        if self.lock.locked():
            raise HomeAssistantError('Já existe uma operação Golmar em curso; o pedido não foi colocado em espera.')
        await self.lock.acquire()

        def guarded_operation():
            try:
                return operation(self.host,self.port,self.password)
            finally:
                # Only the actual executor completion can release the exclusion.
                hass.loop.call_soon_threadsafe(self.lock.release)

        try:
            future = hass.async_add_executor_job(guarded_operation)
        except BaseException:
            self.lock.release()
            raise
        # A cancelled UI/service caller must neither cancel the executor future
        # nor release the exclusion while a socket operation is still running.
        future.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        return await asyncio.shield(future)

    async def async_check(self, hass: HomeAssistant) -> None:
        try:
            result = await self._async_run(hass,test_connection)
        except (OSError,ProtocolError,ValueError) as err:
            raise HomeAssistantError('Não foi possível autenticar a ligação local ao Golmar.') from err
        self.info = {**result,'last_successful_authentication':dt_util.utcnow().isoformat(),
                     'real_call_detection':self._events.status if self._events else 'stopped'}

    async def async_open(self, hass: HomeAssistant) -> None:
        def isolated_open(host,port,password):
            try:
                if self._closing:raise ProtocolError('Integração a terminar; abertura não enviada')
                if self._events:self._events.stop()
                with self.video_proxy.exclusive() if self.video_proxy else nullcontext():
                    open_door(host,port,password)
            finally:
                with self._event_lock:
                    if self._events and not self._closing:self._events.start()
        try:
            await self._async_run(hass,isolated_open)
        except (OSError,ProtocolError,ValueError) as err:
            raise HomeAssistantError(f'Não foi possível confirmar a abertura ({err}). Verifique a porta antes de repetir.') from err
