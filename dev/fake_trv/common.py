"""Shared parts of the fake thermostat entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

DOMAIN = "fake_trv"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def ieee(index: int) -> str:
    return f"0x00000000000001{index:02x}"


class FakeEntity(Entity):
    """An entity of one fake thermostat."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, index: int, name: str, prop: str | None) -> None:
        self.index = index
        self.device_name = name
        self._attr_unique_id = f"{ieee(index)}_{prop or 'climate'}_zigbee2mqtt"
        self._attr_name = prop.replace("_", " ") if prop else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ieee(index))},
            name=f"{name} thermostat",
            manufacturer="Bosch",
            model="Radiator thermostat II (fake)",
        )

    def device_state(self) -> dict:
        return self.hass.data[DOMAIN]["devices"].setdefault(self.index, {})
