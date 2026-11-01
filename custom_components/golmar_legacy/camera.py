"""Read-only on-demand camera for the validated legacy video channel."""
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant,entry: ConfigEntry,async_add_entities: AddEntitiesCallback):
    async_add_entities([GolmarCamera(entry)])


class GolmarCamera(Camera):
    _attr_has_entity_name=True
    _attr_translation_key='video'
    _attr_supported_features=CameraEntityFeature.STREAM
    _attr_should_poll=False

    def __init__(self,entry):
        super().__init__()
        self._runtime=entry.runtime_data
        self._attr_unique_id=f'{entry.unique_id}_video'
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,entry.unique_id)},name='Golmar ART 7W legacy',manufacturer='Golmar',model='ART 7W G2+ V03 / G2Call legacy')

    async def async_camera_image(self,width=None,height=None):
        return await self._runtime.async_snapshot(self.hass)

    async def stream_source(self):
        return self._runtime.video_proxy.url
