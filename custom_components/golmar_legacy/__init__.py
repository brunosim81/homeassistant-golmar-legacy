"""Local integration for Golmar ART 7W V03 / G2Call legacy."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import DEFAULT_PORT
from .protocol import AuthenticationError, ProtocolError, test_connection
from .runtime import GolmarRuntime

PLATFORMS = [Platform.BUTTON,Platform.CAMERA,Platform.EVENT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = GolmarRuntime(entry.data[CONF_HOST],DEFAULT_PORT,entry.data[CONF_PASSWORD])
    try:
        info = await hass.async_add_executor_job(test_connection,runtime.host,runtime.port,runtime.password)
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed('Contra-senha Golmar recusada') from err
    except (OSError,ProtocolError,ValueError) as err:
        raise ConfigEntryNotReady('O Golmar não respondeu à autenticação local') from err
    runtime.info = info
    entry.runtime_data = runtime
    await hass.async_add_executor_job(runtime.start_media)
    try:
        await hass.config_entries.async_forward_entry_setups(entry,PLATFORMS)
    except BaseException:
        await hass.async_add_executor_job(runtime.stop_media)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded=await hass.config_entries.async_unload_platforms(entry,PLATFORMS)
    if unloaded:await hass.async_add_executor_job(entry.runtime_data.stop_media)
    return unloaded
