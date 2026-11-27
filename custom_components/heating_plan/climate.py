"""Climate entity of a room.

- A new temperature is a timed override (default 3 h).
- heat: an override with the current target until cancel.
- off: pause until cancel.
- auto: cancel the override.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingPlanConfigEntry
from .controller import RoomController
from .entity import RoomEntity
from .model import MAX_TEMP

PARALLEL_UPDATES = 0

_MODES = {"auto": HVACMode.AUTO, "heat": HVACMode.HEAT, "off": HVACMode.OFF}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingPlanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one climate entity for each room."""
    for room in entry.runtime_data.rooms.values():
        async_add_entities([RoomClimate(room)], config_subentry_id=room.room_id)


class RoomClimate(RoomEntity, ClimateEntity):
    """The heating of one room."""

    _attr_name = None
    _attr_translation_key = "room"
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_max_temp = MAX_TEMP

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "climate")

    @property
    def min_temp(self) -> float:
        """The minimum target."""
        return self.room.home.options.min_temp

    @property
    def available(self) -> bool:
        """The entity is available when the thermostat is."""
        return self.room.trv.available

    @property
    def hvac_mode(self) -> HVACMode:
        """auto, heat or off."""
        return _MODES[self.room.mode]

    @property
    def target_temperature(self) -> float | None:
        """The target now (None in pause)."""
        return self.room.target

    @property
    def current_temperature(self) -> float | None:
        """The room temperature."""
        return self.room.current_temp

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Override and plan details."""
        override = self.room.override
        change = self.room.next_change
        return {
            "override_until": (
                override.until.isoformat() if override and override.until else None
            ),
            "override_kind": override.kind if override else None,
            "override_source": override.source if override else None,
            "next_change_at": change[0].isoformat() if change else None,
            "next_change_temp": change[1] if change else None,
            "sync_state": self.room.sync_state,
            "last_error": self.room.last_error,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """A new temperature: a timed override."""
        await self.room.set_override(kwargs[ATTR_TEMPERATURE], "timed")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the mode."""
        if hvac_mode == HVACMode.AUTO:
            await self.room.cancel_override()
        elif hvac_mode == HVACMode.HEAT:
            target = self.room.target
            if target is None:
                target = self.room.target_now()
            if target is None:
                target = self.room.plan_temp_now()
            await self.room.set_override(target, "until_cancel")
        elif hvac_mode == HVACMode.OFF:
            await self.room.set_override(None, "until_cancel")

    async def async_turn_on(self) -> None:
        """Turn on: follow the plan again."""
        await self.room.cancel_override()

    async def async_turn_off(self) -> None:
        """Turn off: pause until cancel."""
        await self.room.set_override(None, "until_cancel")
