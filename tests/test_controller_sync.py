"""Tests for writing the effective 7-day plan to the thermostat."""

from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.heating_plan import controller as controller_module
from custom_components.heating_plan.const import DEFAULT_PROFILES
from custom_components.heating_plan.model import DayProfile, PlanError, RoomPlan

from .helpers import settle, setup_home

TZ = ZoneInfo("Europe/Berlin")
WED = date(2026, 12, 2)
WORK = DEFAULT_PROFILES["Praca"]
FREE = DEFAULT_PROFILES["Wolne"]
# Wednesday first: the week is written from today.
EXPECTED_WEEK = [(2, WORK), (3, WORK), (4, WORK), (5, FREE), (6, FREE), (0, WORK), (1, WORK)]


@pytest.fixture(autouse=True)
def wednesday(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(datetime(2026, 12, 2, 9, 0, tzinfo=TZ))


@pytest.fixture(autouse=True)
def short_readback():
    with patch.object(controller_module, "READBACK_TIMEOUT_PER_DAY", 0):
        yield


async def test_sync_writes_seven_days_then_schedule(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    assert trv.day_writes() == EXPECTED_WEEK
    modes = trv.services("select", "select_option")
    assert [m["option"] for m in modes] == ["schedule"]
    # The mode is set after all days.
    assert trv.calls[-1][0] == "select"
    assert s.room().sync_state == "synced"
    assert s.room().last_error is None


async def test_no_writes_when_thermostat_has_the_plan(hass: HomeAssistant) -> None:
    def before(s) -> None:
        for weekday, text in EXPECTED_WEEK:
            s.trvs[0].set_day(weekday, text)
        s.trvs[0].set_mode("schedule")

    s = await setup_home(hass, before_setup=before)
    assert s.trvs[0].calls == []
    assert s.room().sync_state == "synced"


async def test_day_in_other_format_is_equal(hass: HomeAssistant) -> None:
    def before(s) -> None:
        for weekday, text in EXPECTED_WEEK:
            s.trvs[0].set_day(weekday, text)
        s.trvs[0].set_day(2, "6:00/21.0 08:00/18 16:00/21 22:00/17.0")

    s = await setup_home(hass, before_setup=before)
    assert s.trvs[0].day_writes() == []


async def test_no_schedule_until_readback_matches(hass: HomeAssistant) -> None:
    def before(s) -> None:
        s.trvs[0].lose_days = {4}

    s = await setup_home(hass, before_setup=before)
    trv = s.trvs[0]
    writes = trv.day_writes()
    assert writes[:7] == EXPECTED_WEEK
    assert writes[7:] == [(4, WORK)]  # one retry
    assert trv.services("select", "select_option") == []
    assert s.room().sync_state == "error"
    assert "friday" in (s.room().last_error or "")

    # A periodic refresh does not write the same days again.
    trv.clear_calls()
    await s.home.refresh_calendar()
    await settle(hass)
    assert trv.day_writes() == []


async def test_late_readback_is_accepted(hass: HomeAssistant) -> None:
    def before(s) -> None:
        s.trvs[0].late_days = {2, 4}

    with patch.object(controller_module, "READBACK_TIMEOUT_PER_DAY", 30):
        s = await setup_home(hass, before_setup=before)
    assert s.trvs[0].day_writes() == EXPECTED_WEEK
    assert s.room().sync_state == "synced"
    assert [m["option"] for m in s.trvs[0].services("select", "select_option")] == [
        "schedule"
    ]


async def test_never_writes_empty_day(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.clear_calls()
    empty = object.__new__(DayProfile)
    object.__setattr__(empty, "points", ())
    plan = RoomPlan(profiles={"Praca": empty}, week=("Praca",) * 7)
    with pytest.raises(PlanError):
        await s.room().set_plan(plan)
    bad_week = RoomPlan(profiles={"Praca": DayProfile.parse(WORK)}, week=("Brak",) * 7)
    with pytest.raises(PlanError):
        await s.room().set_plan(bad_week)
    await settle(hass)
    assert trv.calls == []


async def test_set_plan_writes_changed_days_and_saves(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.clear_calls()
    plan = RoomPlan(
        profiles={"Praca": DayProfile.parse(WORK), "Wolne": DayProfile.parse("08:00/20")},
        week=("Praca",) * 5 + ("Wolne",) * 2,
    )
    await s.room().set_plan(plan)
    await settle(hass)
    assert trv.day_writes() == [(5, "08:00/20"), (6, "08:00/20")]
    assert s.room().plan == plan
    data = await s.home.store.load()
    assert data["plans"][s.room().room_id] == plan


async def test_offline_trv_pending_then_single_sync(hass: HomeAssistant) -> None:
    def before(s) -> None:
        s.trvs[0].set_available(False)

    s = await setup_home(hass, before_setup=before)
    trv = s.trvs[0]
    assert s.room().sync_state == "pending"
    assert trv.calls == []

    # Other triggers do not write while the thermostat is offline.
    await s.home.refresh_calendar()
    await settle(hass)
    assert trv.calls == []
    assert s.room().sync_state == "pending"

    trv.set_available(True)
    await settle(hass)
    assert trv.day_writes() == EXPECTED_WEEK
    assert s.room().sync_state == "synced"

    # A new state of an available thermostat does not start a new sync.
    trv.clear_calls()
    trv.set_setpoint(19)
    await settle(hass)
    assert trv.calls == []


async def test_midnight_rollover_writes_day_for_next_week(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    def before(s) -> None:
        s.calendar.add("Wolne", WED + timedelta(days=7), WED + timedelta(days=8))

    s = await setup_home(hass, before_setup=before)
    trv = s.trvs[0]
    trv.clear_calls()
    freezer.move_to(datetime(2026, 12, 3, 0, 5, tzinfo=TZ))
    async_fire_time_changed(hass)
    await settle(hass)
    assert trv.day_writes() == [(2, FREE)]


async def test_new_rule_for_this_week_rewrites_that_day(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.clear_calls()
    friday = date(2026, 12, 4)
    s.calendar.add("Wolne", friday, friday + timedelta(days=1))
    await s.home.refresh_calendar()
    await settle(hass)
    assert trv.day_writes() == [(4, FREE)]
    assert trv.services("climate", "set_temperature") == []


async def test_calendar_refresh_every_15_minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_home(hass)
    calls = s.calendar.calls
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await settle(hass)
    assert s.calendar.calls == calls + 1


async def test_rule_for_today_also_sets_current_point(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 12, 2, 10, 0, tzinfo=TZ))
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.clear_calls()
    s.calendar.add("Wolne", WED, WED + timedelta(days=1))
    await s.home.refresh_calendar()
    await settle(hass)
    assert trv.day_writes() == [(2, FREE)]
    # Wolne at 10:00 = 21 (point 07:30/21).
    assert [c["temperature"] for c in trv.services("climate", "set_temperature")] == [21]


async def test_holidays_change_the_profile(hass: HomeAssistant) -> None:
    # 2026-12-24 is not a holiday in PL, 2026-12-25 (Friday) is.
    s = await setup_home(hass, options={"holidays_enabled": True})
    assert date(2026, 12, 25) in s.home.holidays
    friday_write = [w for w in s.trvs[0].day_writes() if w[0] == 4]
    assert friday_write == [(4, WORK)]  # 2026-12-04 is a normal Friday


async def test_unload_stops_timers(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_home(hass)
    assert await hass.config_entries.async_unload(s.entry.entry_id)
    calls = s.calendar.calls
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await settle(hass)
    assert s.calendar.calls == calls
