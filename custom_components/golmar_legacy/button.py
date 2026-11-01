"""Diagnostic login and explicit opening; opening starts disabled."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime import GolmarRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([GolmarConnectionButton(entry),GolmarOpenButton(entry),GolmarSimulateButton(entry)])


class GolmarButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self,entry: ConfigEntry, suffix: str) -> None:
        self._runtime: GolmarRuntime = entry.runtime_data
        self._attr_unique_id = f'{entry.unique_id}_{suffix}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN,entry.unique_id)},name='Golmar ART 7W legacy',manufacturer='Golmar',model='ART 7W G2+ V03 / G2Call legacy')


class GolmarConnectionButton(GolmarButton):
    _attr_translation_key = 'test_connection'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = 'mdi:lan-check'

    def __init__(self,entry: ConfigEntry) -> None:
        super().__init__(entry,'test_connection')

    @property
    def extra_state_attributes(self):
        return self._runtime.info

    async def async_press(self) -> None:
        await self._runtime.async_check(self.hass)
        self.async_write_ha_state()


class GolmarOpenButton(GolmarButton):
    _attr_translation_key = 'open_door'
    _attr_icon = 'mdi:door-open'
    _attr_entity_registry_enabled_default = False

    def __init__(self,entry: ConfigEntry) -> None:
        super().__init__(entry,'open_door')

    async def async_press(self) -> None:
        await self._runtime.async_open(self.hass)


class GolmarSimulateButton(GolmarButton):
    _attr_translation_key='simulate_doorbell'
    _attr_entity_category=EntityCategory.DIAGNOSTIC
    _attr_icon='mdi:bell-alert-outline'

    def __init__(self,entry):
        super().__init__(entry,'simulate_doorbell')

    async def async_press(self):
        self._runtime.simulate_doorbell()
