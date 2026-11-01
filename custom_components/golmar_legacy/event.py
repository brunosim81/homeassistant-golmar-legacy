"""Doorbell events from the validated local ring and explicit test button."""
from homeassistant.components.event import EventEntity, EventDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant,entry: ConfigEntry,async_add_entities: AddEntitiesCallback):
    async_add_entities([GolmarDoorbell(entry)])


class GolmarDoorbell(EventEntity):
    _attr_has_entity_name=True
    _attr_translation_key='doorbell'
    _attr_device_class=EventDeviceClass.DOORBELL
    _attr_event_types=['ring']
    _attr_should_poll=False

    def __init__(self,entry):
        self._runtime=entry.runtime_data
        self._attr_unique_id=f'{entry.unique_id}_doorbell'
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,entry.unique_id)},name='Golmar ART 7W legacy',manufacturer='Golmar',model='ART 7W G2+ V03 / G2Call legacy')

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.add_doorbell_listener(self._receive))

    def _receive(self,attributes):
        self._trigger_event('ring',attributes)
        self.async_write_ha_state()
