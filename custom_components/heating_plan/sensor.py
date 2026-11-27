"""Sensors of a room: next change, override end and plan state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingPlanConfigEntry
from .controller import RoomController
from .entity import RoomEntity

PARALLEL_UPDATES = 0

SYNC_STATES = ["synced", "pending", "error"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingPlanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of each room."""
    for room in entry.runtime_data.rooms.values():
        async_add_entities(
            [NextChangeSensor(room), OverrideUntilSensor(room), SyncStateSensor(room)],
            config_subentry_id=room.room_id,
        )


class NextChangeSensor(RoomEntity, SensorEntity):
    """When the target changes next, and the new target."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_change"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "next_change")

    @property
    def native_value(self) -> datetime | None:
        """The time of the next change."""
        change = self.room.next_change
        return change[0] if change else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The new target (None = pause)."""
        change = self.room.next_change
        return {"temperature": change[1] if change else None}


class OverrideUntilSensor(RoomEntity, SensorEntity):
    """The end of the override."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "override_until"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "override_until")

    @property
    def native_value(self) -> datetime | None:
        """The end of the override, if it has one."""
        override = self.room.override
        return override.until if override else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The override details."""
        override = self.room.override
        return {
            "active": override is not None,
            "temperature": override.temp if override else None,
            "kind": override.kind if override else None,
            "source": override.source if override else None,
        }


class SyncStateSensor(RoomEntity, SensorEntity):
    """Is the plan on the thermostat: synced, pending or error."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SYNC_STATES
    _attr_translation_key = "sync_state"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "sync_state")

    @property
    def native_value(self) -> str:
        """The plan state."""
        return self.room.sync_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The last error."""
        return {"last_error": self.room.last_error}
