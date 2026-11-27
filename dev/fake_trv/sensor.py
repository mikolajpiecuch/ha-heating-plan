"""Fake setpoint change source."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback

from .common import DOMAIN, FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities(FakeSource(i, name) for i, name in enumerate(hass.data[DOMAIN]["names"]))


class FakeSource(FakeEntity, SensorEntity):
    def __init__(self, index: int, name: str) -> None:
        super().__init__(index, name, "setpoint_change_source")
        self._attr_native_value = "manual"

    async def async_added_to_hass(self) -> None:
        self.device_state()["source"] = self

    @callback
    def set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
