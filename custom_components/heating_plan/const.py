"""Constants and configuration types for the Heating Plan integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .model import DayProfile, RoomPlan

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

DOMAIN = "heating_plan"

SUBENTRY_ROOM = "room"

CONF_NAME = "name"
CONF_AREA_ID = "area_id"
CONF_CLIMATE_ENTITY_ID = "climate_entity_id"
CONF_SENSOR_ENTITY_ID = "sensor_entity_id"
CONF_OVERRIDE_HOURS = "override_hours"

CONF_CALENDAR_ENTITY_ID = "calendar_entity_id"
CONF_HOLIDAYS_ENABLED = "holidays_enabled"
CONF_HOLIDAY_PROFILE = "holiday_profile"
CONF_MIN_TEMP = "min_temp"

DEFAULT_OVERRIDE_HOURS = 3.0
DEFAULT_HOLIDAYS_ENABLED = True
DEFAULT_HOLIDAY_PROFILE = "Wolne"
DEFAULT_MIN_TEMP = 7.0
HOLIDAY_COUNTRY = "PL"

DEFAULT_PROFILES = {
    "Praca": "06:00/21 08:00/18 16:00/21 22:00/17",
    "Wolne": "07:30/21 23:00/17",
}
DEFAULT_WEEK = ("Praca",) * 5 + ("Wolne",) * 2


def default_plan() -> RoomPlan:
    """The plan of a new room."""
    return RoomPlan(
        profiles={name: DayProfile.parse(text) for name, text in DEFAULT_PROFILES.items()},
        week=DEFAULT_WEEK,
    )


@dataclass(frozen=True)
class RoomConfig:
    """The configuration of one room (a config subentry)."""

    name: str
    area_id: str
    climate_entity_id: str
    sensor_entity_id: str | None
    override_hours: float = DEFAULT_OVERRIDE_HOURS

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> RoomConfig:
        """Make the config from subentry data."""
        return cls(
            name=data[CONF_NAME],
            area_id=data[CONF_AREA_ID],
            climate_entity_id=data[CONF_CLIMATE_ENTITY_ID],
            sensor_entity_id=data.get(CONF_SENSOR_ENTITY_ID) or None,
            override_hours=float(data.get(CONF_OVERRIDE_HOURS, DEFAULT_OVERRIDE_HOURS)),
        )

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> RoomConfig:
        """Make the config from a room subentry."""
        return cls.from_data(subentry.data)


@dataclass(frozen=True)
class Options:
    """The options of the whole home."""

    calendar_entity_id: str | None = None
    holidays_enabled: bool = DEFAULT_HOLIDAYS_ENABLED
    holiday_profile: str = DEFAULT_HOLIDAY_PROFILE
    min_temp: float = DEFAULT_MIN_TEMP

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> Options:
        """Read the options of a config entry."""
        opts = entry.options
        return cls(
            calendar_entity_id=opts.get(CONF_CALENDAR_ENTITY_ID) or None,
            holidays_enabled=bool(opts.get(CONF_HOLIDAYS_ENABLED, DEFAULT_HOLIDAYS_ENABLED)),
            holiday_profile=opts.get(CONF_HOLIDAY_PROFILE, DEFAULT_HOLIDAY_PROFILE),
            min_temp=float(opts.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)),
        )
