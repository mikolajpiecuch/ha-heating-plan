"""Adapter for a Bosch Radiator Thermostat II (BTH-RA) in Zigbee2MQTT.

The adapter uses only the Home Assistant entities that Zigbee2MQTT makes
for the device (2.14.3 or later). It finds them in the entity registry by the
suffix of their unique id, for example ``0x..._operating_mode_zigbee2mqtt``.
"""

from __future__ import annotations

from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .model import DayProfile

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

MODE_SCHEDULE = "schedule"
MODE_MANUAL = "manual"
MODE_PAUSE = "pause"

SOURCE_MANUAL = "manual"
SOURCE_SCHEDULE = "schedule"
SOURCE_EXTERNALLY = "externally"

REMOTE_TEMP_STEP = 0.2


class TrvError(Exception):
    """The thermostat entities cannot be found."""


def _matches(unique_id: str, prop: str, *, exact: bool) -> bool:
    """Zigbee2MQTT unique id: ``<ieee>_<property>_<base topic>``."""
    if exact:
        return unique_id.endswith(f"_{prop}_zigbee2mqtt")
    # Another base topic, or no base topic.
    return unique_id.endswith(f"_{prop}") or (
        f"_{prop}_" in unique_id and "_" not in unique_id.rsplit(f"_{prop}_", 1)[1]
    )


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    return state.state


class BoschTrv:
    """The Zigbee2MQTT entities of one thermostat."""

    def __init__(
        self,
        hass: HomeAssistant,
        climate_entity_id: str,
        mode_entity_id: str,
        source_entity_id: str,
        remote_entity_id: str,
        day_entity_ids: list[str],
    ) -> None:
        self.hass = hass
        self.climate_entity_id = climate_entity_id
        self.mode_entity_id = mode_entity_id
        self.source_entity_id = source_entity_id
        self.remote_entity_id = remote_entity_id
        self.day_entity_ids = day_entity_ids

    @classmethod
    def from_climate(cls, hass: HomeAssistant, climate_entity_id: str) -> BoschTrv:
        """Find the entities of the device that has the climate entity."""
        registry = er.async_get(hass)
        climate = registry.async_get(climate_entity_id)
        if climate is None or climate.device_id is None:
            raise TrvError(f"{climate_entity_id} is not an entity of a device")
        entries = er.async_entries_for_device(registry, climate.device_id)

        def find(domain: str, prop: str) -> str:
            for exact in (True, False):
                for entry in entries:
                    if entry.domain == domain and _matches(
                        entry.unique_id, prop, exact=exact
                    ):
                        return entry.entity_id
            raise TrvError(
                f"The device of {climate_entity_id} has no {domain} entity "
                f"for '{prop}'. Zigbee2MQTT 2.14.3 or later is necessary"
            )

        return cls(
            hass,
            climate_entity_id,
            find("select", "operating_mode"),
            find("sensor", "setpoint_change_source"),
            find("number", "remote_temperature"),
            [find("text", f"weekly_schedule_{day}") for day in DAYS],
        )

    @property
    def entity_ids(self) -> list[str]:
        """All entities that the adapter reads."""
        return [
            self.climate_entity_id,
            self.mode_entity_id,
            self.source_entity_id,
            *self.day_entity_ids,
        ]

    @property
    def available(self) -> bool:
        """True if the thermostat is online."""
        return _state(self.hass, self.climate_entity_id) is not None

    @property
    def mode(self) -> str | None:
        """The operating mode: schedule, manual or pause."""
        return _state(self.hass, self.mode_entity_id)

    @property
    def change_source(self) -> str | None:
        """What changed the setpoint last: manual, schedule or externally."""
        return _state(self.hass, self.source_entity_id)

    @property
    def setpoint(self) -> float | None:
        """The target temperature of the thermostat."""
        if not self.available:
            return None
        state = self.hass.states.get(self.climate_entity_id)
        value = state.attributes.get(ATTR_TEMPERATURE) if state else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def day_text(self, weekday: int) -> str:
        """The day as the thermostat reports it ("" if not known)."""
        return _state(self.hass, self.day_entity_ids[weekday]) or ""

    async def set_mode(self, mode: str) -> None:
        """Set the operating mode."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {ATTR_ENTITY_ID: self.mode_entity_id, "option": mode},
            blocking=True,
        )

    async def set_setpoint(self, temp: float) -> None:
        """Set the target temperature."""
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {ATTR_ENTITY_ID: self.climate_entity_id, ATTR_TEMPERATURE: temp},
            blocking=True,
        )

    async def write_day(self, weekday: int, profile: DayProfile) -> None:
        """Write one day of the weekly schedule."""
        await self.hass.services.async_call(
            "text",
            "set_value",
            {ATTR_ENTITY_ID: self.day_entity_ids[weekday], "value": profile.to_z2m()},
            blocking=True,
        )

    async def push_remote_temp(self, temp: float) -> None:
        """Send the room temperature, rounded to 0.2 °C."""
        value = round(round(temp / REMOTE_TEMP_STEP) * REMOTE_TEMP_STEP, 1)
        await self.hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: self.remote_entity_id, "value": value},
            blocking=True,
        )
