"""HA adapter tests using the real installed Home Assistant 2026.10.4 API."""
import importlib
import importlib.util
import sys
import logging
from datetime import timedelta
from homeassistant.helpers.entity_platform import EntityPlatform
from pathlib import Path
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from test_protocol import panel, handshake

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT))


def adapter(name):
    path=ROOT/'custom_components/golmar_legacy'/f'{name}.py'
    assert path.exists(),f'Missing Home Assistant adapter: {name}'
    return importlib.import_module(f'custom_components.golmar_legacy.{name}')


@pytest.fixture
async def hass(tmp_path):
    hass=HomeAssistant(str(tmp_path))
    hass.config_entries=ConfigEntries(hass,{})
    yield hass
    await hass.async_stop(force=True)


def entry():
    return ConfigEntry(domain='golmar_legacy',data={CONF_HOST:'192.168.50.20',CONF_PASSWORD:'test-fixture'},minor_version=1,version=1,options={},source='user',title='Golmar legacy',unique_id='192.168.50.20',discovery_keys=MappingProxyType({}),subentries_data=[])


async def test_config_flow_rejects_public_destination_without_network(hass):
    module=adapter('config_flow');flow=module.GolmarLegacyConfigFlow();flow.hass=hass
    result=await flow.async_step_user({CONF_HOST:'8.8.8.8',CONF_PASSWORD:'2108'})
    assert result['type']=='form' and result['errors']=={'host':'invalid_host'}


async def test_connection_button_uses_real_read_only_login_and_door_is_disabled(hass):
    module=adapter('button');runtime_mod=adapter('runtime')
    def serve(listener):
        with listener.accept()[0] as conn:
            handshake(conn);assert conn.recv(1)==b''
    with panel(serve) as port:
        runtime=runtime_mod.GolmarRuntime('127.0.0.1',port,'2108')
        e=entry();e.runtime_data=runtime
        created=[]
        await module.async_setup_entry(hass,e,created.extend)
        assert len(created)==3
        test_button,door_button,simulate_button=created
        assert test_button.entity_category.value=='diagnostic'
        assert door_button.entity_registry_enabled_default is False
        test_button.hass=hass
        test_button.entity_id="button.golmar_test_connection"
        test_button.platform=EntityPlatform(hass=hass,logger=logging.getLogger(__name__),domain="button",platform_name="golmar_legacy",platform=None,scan_interval=timedelta(seconds=60),entity_namespace=None)
        await test_button.async_press()
        assert test_button.extra_state_attributes['protocol_version']=='0x213'
        assert test_button.extra_state_attributes['last_successful_authentication']


async def test_busy_operation_cannot_queue_another_opening(hass):
    runtime_mod=adapter('runtime');runtime=runtime_mod.GolmarRuntime('127.0.0.1',1,'2108')
    async with runtime.lock:
        with pytest.raises(HomeAssistantError,match='curso'):
            await runtime.async_open(hass)


async def test_password_does_not_appear_in_runtime_repr(hass):
    runtime_mod=adapter('runtime');runtime=runtime_mod.GolmarRuntime('127.0.0.1',1,'private-test-secret')
    assert 'private-test-secret' not in repr(runtime)


@pytest.mark.parametrize('status,expected',[(0,'create_entry'),(4,'form')])
async def test_config_flow_authenticates_without_device_commands(hass,monkeypatch,status,expected):
    module=adapter('config_flow')
    client=adapter('protocol')
    def serve(listener):
        with listener.accept()[0] as conn:
            handshake(conn,status=status);assert conn.recv(1)==b''
    original_connect=client.socket.create_connection
    with panel(serve) as port:
        # Redirect only the socket to loopback, keeping real config-flow and client code.
        monkeypatch.setattr(client.socket,'create_connection',lambda address,timeout:original_connect(('127.0.0.1',port),timeout))
        flow=module.GolmarLegacyConfigFlow();flow.hass=hass;flow.context={'source':'user'}
        result=await flow.async_step_user({CONF_HOST:'192.168.50.20',CONF_PASSWORD:'2108'})
    assert result['type']==expected
    if status==0:
        assert result['data']=={CONF_HOST:'192.168.50.20',CONF_PASSWORD:'2108'}
    else:
        assert result['errors']=={'base':'invalid_auth'}


async def test_cancelled_caller_keeps_opening_busy_until_executor_finishes(hass,monkeypatch):
    import asyncio
    import threading
    module=adapter('runtime');runtime=module.GolmarRuntime('127.0.0.1',1,'2108')
    started=threading.Event();finish=threading.Event();calls=[]
    def simulated_open(*args):
        calls.append(1);started.set();finish.wait(timeout=3)
    monkeypatch.setattr(module,'open_door',simulated_open)
    task=asyncio.create_task(runtime.async_open(hass))
    try:
        for _ in range(100):
            if started.is_set():break
            await asyncio.sleep(.01)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert runtime.lock.locked(), 'Cancelling the caller must not unlock a running executor'
        with pytest.raises(HomeAssistantError,match='curso'):await runtime.async_open(hass)
        assert len(calls)==1
    finally:
        finish.set()
        for _ in range(100):
            if not runtime.lock.locked():break
            await asyncio.sleep(.01)
    assert not runtime.lock.locked()


async def test_simulated_doorbell_event_is_labelled_and_never_opens(hass):
    import asyncio
    module=adapter('event');runtime_mod=adapter('runtime');runtime=runtime_mod.GolmarRuntime('127.0.0.1',1,'2108')
    e=entry();e.runtime_data=runtime;entities=[]
    await module.async_setup_entry(hass,e,entities.extend)
    assert len(entities)==1
    event=entities[0];event.hass=hass;event.entity_id='event.golmar_campainha'
    event.platform=EntityPlatform(hass=hass,logger=logging.getLogger(__name__),domain='event',platform_name='golmar_legacy',platform=None,scan_interval=timedelta(seconds=60),entity_namespace=None)
    await event.async_added_to_hass()
    runtime.simulate_doorbell()
    assert event.state is not None
    assert event.state_attributes['event_type']=='ring'
    assert event.state_attributes['simulated'] is True
    assert event.state_attributes['source']=='manual_test'
    await event.async_will_remove_from_hass()


async def test_camera_and_event_share_existing_device_identity(hass):
    module=adapter('camera');events=adapter('event');runtime_mod=adapter('runtime');runtime=runtime_mod.GolmarRuntime('127.0.0.1',1,'2108');e=entry();e.runtime_data=runtime
    camera=module.GolmarCamera(e);bell=events.GolmarDoorbell(e)
    assert camera.device_info['identifiers']==bell.device_info['identifiers']=={('golmar_legacy',e.unique_id)}
    assert camera.supported_features.value & 2
    assert bell.device_class.value=='doorbell'
