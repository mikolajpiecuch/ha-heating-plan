"""Base entity for a room."""

from __future__ import annotations

from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .controller import RoomController


def room_device_info(room: RoomController) -> DeviceInfo:
    """The device of a room."""
    return DeviceInfo(
        identifiers={(DOMAIN, room.room_id)},
        name=room.name,
        manufacturer="Heating Plan",
        model="Room",
    )


class RoomEntity(Entity):
    """An entity of one room. It updates when the room or its thermostat changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, room: RoomController, key: str) -> None:
        self.room = room
        self._attr_unique_id = f"{room.room_id}_{key}"
        self._attr_device_info = room_device_info(room)

    async def async_added_to_hass(self) -> None:
        """Follow the room and the thermostat."""
        self.async_on_remove(self.room.async_add_listener(self._room_changed))
        entities = [self.room.trv.climate_entity_id, self.room.trv.mode_entity_id]
        if self.room.room.sensor_entity_id:
            entities.append(self.room.room.sensor_entity_id)
        self.async_on_remove(
            async_track_state_change_event(self.hass, entities, self._state_changed)
        )

    @callback
    def _room_changed(self) -> None:
        self.async_write_ha_state()

    @callback
    def _state_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()
