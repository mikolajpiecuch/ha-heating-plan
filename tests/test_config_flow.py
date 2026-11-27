"""Tests for the config flow, the room subentries, the options and the store."""

from datetime import UTC, datetime, timedelta, timezone

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_plan.const import (
    CONF_AREA_ID,
    CONF_CALENDAR_ENTITY_ID,
    CONF_CLIMATE_ENTITY_ID,
    CONF_HOLIDAY_PROFILE,
    CONF_HOLIDAYS_ENABLED,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_OVERRIDE_HOURS,
    CONF_SENSOR_ENTITY_ID,
    DOMAIN,
    SUBENTRY_ROOM,
    Options,
    RoomConfig,
    default_plan,
)
from custom_components.heating_plan.model import DayProfile, Override
from custom_components.heating_plan.store import PlanStore

from .fake_trv import add_fake_trvs


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The user step creates an entry with the given name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Dom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dom"


async def test_only_one_entry(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, title="Dom").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A created entry loads and unloads."""
    entry = MockConfigEntry(domain=DOMAIN, title="Dom")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Dom")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add_room(hass: HomeAssistant, entry: MockConfigEntry, data: dict) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], data
    )
    await hass.async_block_till_done()
    return result


async def test_add_room(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    area = ar.async_get(hass).async_create("Office")
    hass.states.async_set("sensor.office_air", "20.5", {"device_class": "temperature"})
    entry = await _setup_entry(hass)

    result = await _add_room(
        hass,
        entry,
        {
            CONF_NAME: "Biuro",
            CONF_AREA_ID: area.id,
            CONF_CLIMATE_ENTITY_ID: fake.climate,
            CONF_SENSOR_ENTITY_ID: "sensor.office_air",
            CONF_OVERRIDE_HOURS: 2,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    [subentry] = entry.subentries.values()
    assert subentry.title == "Biuro"
    room = RoomConfig.from_subentry(subentry)
    assert room == RoomConfig(
        name="Biuro",
        area_id=area.id,
        climate_entity_id=fake.climate,
        sensor_entity_id="sensor.office_air",
        override_hours=2,
    )

    data = await PlanStore(hass, entry.entry_id).load()
    assert data["plans"][subentry.subentry_id] == default_plan()


async def test_add_room_without_sensor(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    area = ar.async_get(hass).async_create("Office")
    entry = await _setup_entry(hass)
    result = await _add_room(
        hass,
        entry,
        {CONF_NAME: "Biuro", CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: fake.climate},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    room = RoomConfig.from_subentry(next(iter(entry.subentries.values())))
    assert room.sensor_entity_id is None
    assert room.override_hours == 3


async def test_same_thermostat_twice_aborts(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    area = ar.async_get(hass).async_create("Office")
    entry = await _setup_entry(hass)
    data = {CONF_NAME: "Biuro", CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: fake.climate}
    await _add_room(hass, entry, data)
    result = await _add_room(hass, entry, {**data, CONF_NAME: "Biuro 2"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(entry.subentries) == 1


async def test_room_name_twice_is_an_error(hass: HomeAssistant) -> None:
    [one, two] = add_fake_trvs(hass, ["office", "bedroom"])
    area = ar.async_get(hass).async_create("Office")
    entry = await _setup_entry(hass)
    await _add_room(
        hass, entry, {CONF_NAME: "Biuro", CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: one.climate}
    )
    result = await _add_room(
        hass, entry, {CONF_NAME: "biuro", CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: two.climate}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NAME: "name_exists"}


async def test_reserved_room_name_is_an_error(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    area = ar.async_get(hass).async_create("Office")
    entry = await _setup_entry(hass)
    for name in ("Wolne", "praca", "Wyłącz"):
        result = await _add_room(
            hass,
            entry,
            {CONF_NAME: name, CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: fake.climate},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_NAME: "name_reserved"}


async def test_not_a_bosch_thermostat_is_an_error(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"], skip="weekly_schedule_monday")
    area = ar.async_get(hass).async_create("Office")
    entry = await _setup_entry(hass)
    result = await _add_room(
        hass, entry, {CONF_NAME: "Biuro", CONF_AREA_ID: area.id, CONF_CLIMATE_ENTITY_ID: fake.climate}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CLIMATE_ENTITY_ID: "trv_not_supported"}


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    assert Options.from_entry(entry) == Options(
        calendar_entity_id=None, holidays_enabled=True, holiday_profile="Wolne", min_temp=7
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CALENDAR_ENTITY_ID: "calendar.heating",
            CONF_HOLIDAYS_ENABLED: False,
            CONF_HOLIDAY_PROFILE: "Wolne",
            CONF_MIN_TEMP: 8,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert Options.from_entry(entry) == Options(
        calendar_entity_id="calendar.heating",
        holidays_enabled=False,
        holiday_profile="Wolne",
        min_temp=8,
    )


async def test_store_round_trip(hass: HomeAssistant, hass_storage: dict) -> None:
    store = PlanStore(hass, "entry1")
    assert await store.load() == {"plans": {}, "overrides": {}}

    plan = default_plan()
    plan.profiles["Noc"] = DayProfile.parse("00:00/16")
    until = datetime(2026, 12, 2, 13, 30, tzinfo=timezone(timedelta(hours=1)))
    data = {
        "plans": {"room1": plan},
        "overrides": {
            "room1": Override(temp=23.5, until=until, kind="timed", source="knob"),
            "room2": Override(temp=None, until=None, kind="until_cancel", source="ha"),
        },
    }
    await store.save(data)

    raw = hass_storage[f"{DOMAIN}.entry1"]["data"]
    assert raw["overrides"]["room1"]["until"] == "2026-12-02T12:30:00+00:00"

    loaded = await PlanStore(hass, "entry1").load()
    assert loaded["plans"]["room1"] == plan
    assert loaded["overrides"]["room1"] == Override(
        temp=23.5, until=until.astimezone(UTC), kind="timed", source="knob"
    )
    assert loaded["overrides"]["room2"] == data["overrides"]["room2"]


async def test_store_skips_bad_items(hass: HomeAssistant, hass_storage: dict) -> None:
    hass_storage[f"{DOMAIN}.entry1"] = {
        "version": 1,
        "key": f"{DOMAIN}.entry1",
        "data": {
            "plans": {
                "good": {"profiles": {"A": "06:00/20"}, "week": ["A"] * 7},
                "bad": {"profiles": {"A": ""}, "week": ["A"] * 7},
            },
            "overrides": {
                "good": {"temp": 21, "until": None, "kind": "until_cancel", "source": "ha"},
                "bad": {"temp": 21, "until": "not a time", "kind": "timed", "source": "ha"},
            },
        },
    }
    data = await PlanStore(hass, "entry1").load()
    assert set(data["plans"]) == {"good"}
    assert set(data["overrides"]) == {"good"}


def test_default_plan() -> None:
    plan = default_plan()
    plan.validate()
    assert plan.profiles["Praca"].to_z2m() == "06:00/21 08:00/18 16:00/21 22:00/17"
    assert plan.profiles["Wolne"].to_z2m() == "07:30/21 23:00/17"
    assert plan.week == ("Praca",) * 5 + ("Wolne",) * 2
