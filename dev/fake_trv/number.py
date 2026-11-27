"""Fake remote temperature."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant

from .common import DOMAIN, FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities(FakeRemote(i, name) for i, name in enumerate(hass.data[DOMAIN]["names"]))


class FakeRemote(FakeEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 35
    _attr_native_step = 0.2

    def __init__(self, index: int, name: str) -> None:
        super().__init__(index, name, "remote_temperature")
        self._attr_native_value = None

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
