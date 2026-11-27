"""Config flow for the fake thermostats (import only)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .common import DOMAIN


class FakeTrvFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, data: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Fake thermostats", data={})
