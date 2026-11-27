"""Fake operating mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant

from .common import DOMAIN, FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities(FakeMode(i, name) for i, name in enumerate(hass.data[DOMAIN]["names"]))


class FakeMode(FakeEntity, SelectEntity):
    _attr_options = ["schedule", "manual", "pause"]

    def __init__(self, index: int, name: str) -> None:
        super().__init__(index, name, "operating_mode")
        self._attr_current_option = "manual"

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
