"""Helpers to set up the integration with fake thermostats and a fake calendar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import area_registry as ar

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_plan.const import (
    CONF_AREA_ID,
    CONF_CALENDAR_ENTITY_ID,
    CONF_CLIMATE_ENTITY_ID,
    CONF_HOLIDAYS_ENABLED,
    CONF_NAME,
    CONF_OVERRIDE_HOURS,
    CONF_SENSOR_ENTITY_ID,
    DOMAIN,
    SUBENTRY_ROOM,
)
from custom_components.heating_plan.controller import HomeController, RoomController

from .fake_trv import FakeTrv, add_fake_trvs

CALENDAR = "calendar.heating"


@dataclass
class FakeCalendar:
    """Events that the fake calendar.get_events service returns."""

    events: list[dict] = field(default_factory=list)
    calls: int = 0

    def add(self, summary: str, start: date | datetime, end: date | datetime) -> None:
        self.events.append(
            {"summary": summary, "start": start.isoformat(), "end": end.isoformat()}
        )


def add_fake_calendar(hass: HomeAssistant) -> FakeCalendar:
    calendar = FakeCalendar()
    hass.states.async_set(CALENDAR, "off")

    async def get_events(call: ServiceCall) -> ServiceResponse:
        calendar.calls += 1
        return {CALENDAR: {"events": list(calendar.events)}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    return calendar


@dataclass
class Setup:
    entry: MockConfigEntry
    trvs: list[FakeTrv]
    calendar: FakeCalendar

    @property
    def home(self) -> HomeController:
        return self.entry.runtime_data

    def room(self, index: int = 0) -> RoomController:
        return list(self.home.rooms.values())[index]


async def setup_home(
    hass: HomeAssistant,
    names: tuple[str, ...] = ("Biuro",),
    *,
    sensors: dict[str, str] | None = None,
    options: dict | None = None,
    before_setup=None,
    override_hours: float = 3,
) -> Setup:
    """Add fake thermostats, a calendar and the config entry, then set it up."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    trvs = add_fake_trvs(hass, [f"trv_{i}" for i in range(len(names))])
    calendar = add_fake_calendar(hass)
    areas = ar.async_get(hass)
    subentries = []
    for name, trv in zip(names, trvs, strict=True):
        area = areas.async_get_or_create(name)
        data = {
            CONF_NAME: name,
            CONF_AREA_ID: area.id,
            CONF_CLIMATE_ENTITY_ID: trv.climate,
            CONF_OVERRIDE_HOURS: override_hours,
        }
        if sensors and name in sensors:
            data[CONF_SENSOR_ENTITY_ID] = sensors[name]
        subentries.append(
            ConfigSubentryData(
                data=data, subentry_type=SUBENTRY_ROOM, title=name, unique_id=trv.climate
            )
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dom",
        options={
            CONF_CALENDAR_ENTITY_ID: CALENDAR,
            CONF_HOLIDAYS_ENABLED: False,
            **(options or {}),
        },
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    setup = Setup(entry, trvs, calendar)
    if before_setup is not None:
        before_setup(setup)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return setup


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)
