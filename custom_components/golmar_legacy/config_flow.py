"""Configure one LAN monitor, checking authentication without opening a door."""
from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import DEFAULT_PORT, DOMAIN
from .protocol import AuthenticationError, ProtocolError, derive_password, test_connection

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
})


class GolmarLegacyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str,Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input[CONF_PASSWORD]
            try:
                address = ipaddress.IPv4Address(host)
                if not any(address in network for network in (
                    ipaddress.IPv4Network('192.168.0.0/16'),
                    ipaddress.IPv4Network('172.16.0.0/12'),
                    ipaddress.IPv4Network('10.0.0.0/8'),
                )):
                    raise ValueError
                host = str(address)
            except (ValueError,TypeError):
                errors[CONF_HOST] = 'invalid_host'
            if not errors:
                try:
                    derive_password(password)
                except ValueError:
                    errors[CONF_PASSWORD] = 'invalid_password'
            if not errors:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                try:
                    await self.hass.async_add_executor_job(test_connection,host,DEFAULT_PORT,password)
                except AuthenticationError:
                    errors['base'] = 'invalid_auth'
                except (OSError,ProtocolError):
                    errors['base'] = 'cannot_connect'
                else:
                    return self.async_create_entry(title='Golmar ART 7W legacy',data={CONF_HOST:host,CONF_PASSWORD:password})
        return self.async_show_form(step_id='user',data_schema=SCHEMA,errors=errors)
