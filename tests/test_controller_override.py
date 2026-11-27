"""Tests for overrides, calendar temperature and pause rules, and restarts."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.heating_plan import controller as controller_module
from custom_components.heating_plan.model import Override

from .helpers import Setup, settle, setup_home

TZ = ZoneInfo("Europe/Berlin")
WED = date(2026, 12, 2)


@pytest.fixture(autouse=True)
def short_readback():
    with patch.object(controller_module, "READBACK_TIMEOUT_PER_DAY", 0):
        yield


@pytest.fixture
def at_seven(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(datetime(2026, 12, 2, 7, 0, tzinfo=TZ))


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await settle(hass)


def modes(s: Setup) -> list[str]:
    return [m["option"] for m in s.trvs[0].services("select", "select_option")]


def setpoints(s: Setup) -> list[float]:
    return [c["temperature"] for c in s.trvs[0].services("climate", "set_temperature")]


@pytest.mark.usefixtures("at_seven")
async def test_ha_override_timed(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    s = await setup_home(hass)
    room = s.room()
    trv = s.trvs[0]
    assert trv.hass.states.get(trv.mode_entity).state == "schedule"
    trv.clear_calls()

    await room.set_override(23)
    await settle(hass)
    assert modes(s) == ["manual"]
    assert setpoints(s) == [23]
    assert room.override == Override(
        temp=23, until=dt_util.utcnow() + timedelta(hours=3), kind="timed", source="ha"
    )
    assert room.mode == "auto"
    assert room.target == 23

    trv.clear_calls()
    await tick(hass, freezer, timedelta(hours=2, minutes=59))
    assert modes(s) == []
    await tick(hass, freezer, timedelta(minutes=1))
    assert modes(s) == ["schedule"]
    assert room.override is None


@pytest.mark.usefixtures("at_seven")
async def test_override_hours_from_room_and_call(hass: HomeAssistant) -> None:
    s = await setup_home(hass, override_hours=2)
    await s.room().set_override(22)
    assert s.room().override.until == dt_util.utcnow() + timedelta(hours=2)
    await s.room().set_override(22, hours=0.5)
    assert s.room().override.until == dt_util.utcnow() + timedelta(minutes=30)


@pytest.mark.usefixtures("at_seven")
async def test_until_next_ends_at_next_point(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    await s.room().set_override(23, kind="until_next")
    assert s.room().override.until == datetime(2026, 12, 2, 8, 0, tzinfo=TZ)
    assert s.room().override.kind == "until_next"


@pytest.mark.usefixtures("at_seven")
async def test_until_cancel_never_expires(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_home(hass)
    room = s.room()
    await room.set_override(23, kind="until_cancel")
    assert room.override.until is None
    assert room.mode == "heat"
    assert room.next_change is None
    s.trvs[0].clear_calls()
    await tick(hass, freezer, timedelta(hours=48))
    assert modes(s) == []
    assert room.override is not None
    await room.cancel_override()
    await settle(hass)
    assert modes(s) == ["schedule"]
    assert room.override is None
    assert room.mode == "auto"


@pytest.mark.usefixtures("at_seven")
async def test_pause_override(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    s.trvs[0].clear_calls()
    await s.room().set_override(None, kind="until_cancel")
    await settle(hass)
    assert modes(s) == ["pause"]
    assert setpoints(s) == []
    assert s.room().mode == "off"
    assert s.room().target is None


@pytest.mark.usefixtures("at_seven")
async def test_knob_change_becomes_timed_override(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.clear_calls()
    trv.turn_knob(24)
    await settle(hass)
    override = s.room().override
    assert override == Override(
        temp=24, until=dt_util.utcnow() + timedelta(hours=3), kind="timed", source="knob"
    )
    assert modes(s) == ["manual"]
    assert setpoints(s) == []  # the thermostat has 24 already


@pytest.mark.usefixtures("at_seven")
async def test_knob_again_restarts_timer(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.turn_knob(24)
    await settle(hass)
    await tick(hass, freezer, timedelta(hours=1))
    trv.turn_knob(22.5)
    await settle(hass)
    override = s.room().override
    assert override.temp == 22.5
    assert override.until == dt_util.utcnow() + timedelta(hours=3)
    # The old timer does not end the new override.
    await tick(hass, freezer, timedelta(hours=2, minutes=30))
    assert s.room().override is not None
    await tick(hass, freezer, timedelta(minutes=30))
    assert s.room().override is None


@pytest.mark.usefixtures("at_seven")
async def test_knob_during_ha_override_replaces_it(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    await s.room().set_override(23, kind="until_cancel")
    await settle(hass)
    s.trvs[0].turn_knob(20)
    await settle(hass)
    assert s.room().override.source == "knob"
    assert s.room().override.kind == "timed"
    assert s.room().override.temp == 20


@pytest.mark.usefixtures("at_seven")
async def test_own_write_is_not_knob(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    await s.room().set_override(23)
    await settle(hass)
    assert trv.hass.states.get(trv.source_entity).state == "externally"
    assert s.room().override.source == "ha"

    # After a knob turn the source is "manual". The new setpoint of an own
    # write comes before the new source.
    trv.turn_knob(24)
    await settle(hass)
    assert s.room().override.source == "knob"
    trv.source_on_write = False
    await s.room().set_override(21)
    await settle(hass)
    assert trv.hass.states.get(trv.source_entity).state == "manual"
    assert s.room().override.source == "ha"
    assert s.room().override.temp == 21
    trv.set_source("externally")
    await settle(hass)
    assert s.room().override.source == "ha"


@pytest.mark.usefixtures("at_seven")
async def test_schedule_change_is_not_knob(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    trv = s.trvs[0]
    trv.set_source("schedule")
    trv.set_setpoint(18)
    await settle(hass)
    assert s.room().override is None


async def test_temp_rule_uses_manual_then_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 12, 2, 9, 0, tzinfo=TZ))

    def before(s: Setup) -> None:
        s.calendar.add(
            "Urlop 15",
            datetime(2026, 12, 2, 10, 0, tzinfo=TZ),
            datetime(2026, 12, 2, 12, 0, tzinfo=TZ),
        )

    s = await setup_home(hass, before_setup=before)
    room = s.room()
    s.trvs[0].clear_calls()
    await tick(hass, freezer, timedelta(hours=1))
    assert modes(s) == ["manual"]
    assert setpoints(s) == [15]
    assert room.target == 15
    assert room.mode == "auto"
    assert room.next_change == (datetime(2026, 12, 2, 12, 0, tzinfo=TZ), 18)

    # An override wins over the rule.
    s.trvs[0].clear_calls()
    await room.set_override(22)
    await settle(hass)
    assert setpoints(s) == [22]
    await room.cancel_override()
    await settle(hass)
    assert setpoints(s) == [22, 15]

    s.trvs[0].clear_calls()
    await tick(hass, freezer, timedelta(hours=2))
    assert modes(s) == ["schedule"]


async def test_off_rule_uses_pause(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(datetime(2026, 12, 2, 9, 0, tzinfo=TZ))

    def before(s: Setup) -> None:
        s.calendar.add("Biuro wyłącz", WED, WED + timedelta(days=1))

    s = await setup_home(hass, before_setup=before)
    assert modes(s) == ["pause"]
    assert s.room().target is None


async def test_override_survives_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 12, 2, 7, 0, tzinfo=TZ))
    s = await setup_home(hass)
    await s.room().set_override(23)
    await settle(hass)
    until = s.room().override.until

    await hass.config_entries.async_reload(s.entry.entry_id)
    await settle(hass)
    room = s.room()
    assert room.override == Override(temp=23, until=until, kind="timed", source="ha")
    trv = s.trvs[0]
    assert trv.hass.states.get(trv.mode_entity).state == "manual"
    trv.clear_calls()
    await tick(hass, freezer, timedelta(hours=3))
    assert modes(s) == ["schedule"]
    assert room.override is None


async def test_expired_override_on_start_returns_to_schedule(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 12, 2, 7, 0, tzinfo=TZ))
    s = await setup_home(hass)
    await s.room().set_override(23)
    await settle(hass)
    await hass.config_entries.async_unload(s.entry.entry_id)
    await settle(hass)

    freezer.tick(timedelta(hours=5))
    s.trvs[0].clear_calls()
    assert await hass.config_entries.async_setup(s.entry.entry_id)
    await settle(hass)
    assert s.room().override is None
    assert modes(s) == ["schedule"]
    data = await s.home.store.load()
    assert data["overrides"] == {}


async def test_dst_timed_override_is_3_real_hours(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # 2026-10-25 01:30 CEST; the clock goes back from 03:00 to 02:00.
    start = datetime(2026, 10, 25, 1, 30, tzinfo=TZ)
    freezer.move_to(start)
    s = await setup_home(hass)
    await s.room().set_override(23)
    until = s.room().override.until
    assert until == start.astimezone(UTC) + timedelta(hours=3)
    assert dt_util.as_local(until).hour == 3  # 03:30 CET, not 04:30


@pytest.mark.usefixtures("at_seven")
async def test_cancel_all(hass: HomeAssistant) -> None:
    s = await setup_home(hass, ("Biuro", "Sypialnia"))
    await s.room(0).set_override(23, kind="until_cancel")
    await s.room(1).set_override(None, kind="until_cancel")
    await settle(hass)
    await s.home.cancel_all()
    await settle(hass)
    for index in (0, 1):
        assert s.room(index).override is None
        state = hass.states.get(s.trvs[index].mode_entity).state
        assert state == "schedule"


@pytest.mark.usefixtures("at_seven")
async def test_min_temp_clamps_override(hass: HomeAssistant) -> None:
    s = await setup_home(hass)
    s.trvs[0].clear_calls()
    await s.room().set_override(5)
    await settle(hass)
    assert setpoints(s) == [7]
    assert s.room().override.temp == 7
