"""Tests for resolving the plan, the calendar rules and the holidays."""

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.util import dt as dt_util

from custom_components.heating_plan.calendar_rules import CalendarRule
from custom_components.heating_plan.model import DayProfile, RoomPlan
from custom_components.heating_plan.resolver import (
    device_week,
    local_datetime,
    next_change,
    profile_for_date,
    target_at,
)

TZ = ZoneInfo("Europe/Berlin")
WED = date(2026, 12, 2)
ROOM = "Biuro"
WORK = DayProfile.parse("06:00/21 08:00/18 16:00/21 22:00/17")
FREE = DayProfile.parse("07:30/21 23:00/17")


@pytest.fixture(autouse=True)
def time_zone() -> Generator[None]:
    """Use a time zone with DST."""
    old = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(TZ)
    yield
    dt_util.set_default_time_zone(old)


@pytest.fixture
def plan() -> RoomPlan:
    return RoomPlan(
        profiles={"Praca": WORK, "Wolne": FREE},
        week=("Praca",) * 5 + ("Wolne",) * 2,
    )


def local(day: date, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def day_rule(
    first: date,
    days: int = 1,
    *,
    room: str | None = None,
    profile: str | None = None,
    temp: float | None = None,
    off: bool = False,
) -> CalendarRule:
    return CalendarRule(
        local(first), local(first + timedelta(days=days)), room, profile, temp, off
    )


def test_weekday_uses_work_profile(plan: RoomPlan) -> None:
    assert WED.weekday() == 2
    assert profile_for_date(plan, ROOM, WED, [], set(), None) == "Praca"
    assert profile_for_date(plan, ROOM, date(2026, 12, 5), [], set(), None) == "Wolne"


def test_day_off_event_switches_profile(plan: RoomPlan) -> None:
    rules = [day_rule(WED, profile="Wolne")]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Wolne"
    assert profile_for_date(plan, ROOM, WED + timedelta(1), rules, set(), None) == "Praca"
    assert profile_for_date(plan, ROOM, WED - timedelta(1), rules, set(), None) == "Praca"


def test_multi_day_event_24_to_26_dec(plan: RoomPlan) -> None:
    rules = [day_rule(date(2026, 12, 24), 3, profile="Wolne")]
    for day in (24, 25, 26):
        assert profile_for_date(plan, ROOM, date(2026, 12, day), rules, set(), None) == "Wolne"
    assert profile_for_date(plan, ROOM, date(2026, 12, 23), rules, set(), None) == "Praca"
    assert profile_for_date(plan, ROOM, date(2026, 12, 28), rules, set(), None) == "Praca"


def test_event_over_midnight_touches_both_days(plan: RoomPlan) -> None:
    rules = [
        CalendarRule(local(WED, 22), local(WED + timedelta(1), 2), None, "Wolne", None, False)
    ]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Wolne"
    assert profile_for_date(plan, ROOM, WED + timedelta(1), rules, set(), None) == "Wolne"
    assert profile_for_date(plan, ROOM, WED + timedelta(2), rules, set(), None) == "Praca"


def test_room_rule_beats_house_rule(plan: RoomPlan) -> None:
    rules = [day_rule(WED, room=ROOM, profile="Praca"), day_rule(WED, profile="Wolne")]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Praca"
    assert profile_for_date(plan, "Salon", WED, rules, set(), None) == "Wolne"


def test_rule_for_other_room_ignored(plan: RoomPlan) -> None:
    rules = [day_rule(WED, room="Salon", profile="Wolne")]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Praca"


def test_later_start_wins_same_scope(plan: RoomPlan) -> None:
    rules = [
        day_rule(WED, profile="Praca"),
        day_rule(WED - timedelta(1), 3, profile="Wolne"),
    ]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Praca"
    rules = [
        day_rule(WED - timedelta(1), 3, profile="Praca"),
        day_rule(WED, profile="Wolne"),
    ]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Wolne"


def test_rule_with_unknown_profile_ignored(plan: RoomPlan) -> None:
    rules = [day_rule(WED, profile="Inny")]
    assert profile_for_date(plan, ROOM, WED, rules, set(), None) == "Praca"


def test_holiday_uses_holiday_profile(plan: RoomPlan) -> None:
    day = date(2026, 11, 11)
    assert profile_for_date(plan, ROOM, day, [], {day}, "Wolne") == "Wolne"
    assert profile_for_date(plan, ROOM, day, [], {day}, None) == "Praca"
    assert profile_for_date(plan, ROOM, day, [], {day}, "Brak") == "Praca"


def test_rule_beats_holiday(plan: RoomPlan) -> None:
    day = date(2026, 11, 11)
    rules = [day_rule(day, profile="Praca")]
    assert profile_for_date(plan, ROOM, day, rules, {day}, "Wolne") == "Praca"


def test_target_follows_points(plan: RoomPlan) -> None:
    assert target_at(plan, ROOM, local(WED, 7), [], set(), None, 7) == 21
    assert target_at(plan, ROOM, local(WED, 8), [], set(), None, 7) == 18
    # Before the first point: the last point of Tuesday.
    assert target_at(plan, ROOM, local(WED, 5), [], set(), None, 7) == 17


def test_target_before_first_point_uses_effective_previous_day(plan: RoomPlan) -> None:
    plan.profiles["Noc"] = DayProfile.parse("06:00/21 20:00/19")
    rules = [day_rule(WED - timedelta(1), profile="Noc")]
    assert target_at(plan, ROOM, local(WED, 5), rules, set(), None, 7) == 19


def test_temp_rule_wins_and_min_temp_applies(plan: RoomPlan) -> None:
    rules = [CalendarRule(local(WED, 10), local(WED, 12), None, None, 5, False)]
    assert target_at(plan, ROOM, local(WED, 11), rules, set(), None, 7) == 7
    assert target_at(plan, ROOM, local(WED, 12), rules, set(), None, 7) == 18
    rules = [CalendarRule(local(WED, 10), local(WED, 12), None, None, 15, False)]
    assert target_at(plan, ROOM, local(WED, 10), rules, set(), None, 7) == 15


def test_min_temp_applies_to_plan(plan: RoomPlan) -> None:
    plan.profiles["Praca"] = DayProfile.parse("06:00/5")
    assert target_at(plan, ROOM, local(WED, 7), [], set(), None, 7) == 7


def test_temp_rule_above_max_is_cut(plan: RoomPlan) -> None:
    rules = [CalendarRule(local(WED, 10), local(WED, 12), None, None, 45, False)]
    assert target_at(plan, ROOM, local(WED, 11), rules, set(), None, 7) == 30


def test_off_rule_returns_none(plan: RoomPlan) -> None:
    rules = [CalendarRule(local(WED, 10), local(WED, 12), ROOM, None, None, True)]
    assert target_at(plan, ROOM, local(WED, 11), rules, set(), None, 7) is None


def test_room_temp_rule_beats_house_temp_rule(plan: RoomPlan) -> None:
    rules = [
        CalendarRule(local(WED, 10), local(WED, 12), ROOM, None, 22, False),
        CalendarRule(local(WED, 11), local(WED, 12), None, None, 15, False),
    ]
    assert target_at(plan, ROOM, local(WED, 11), rules, set(), None, 7) == 22
    assert target_at(plan, "Salon", local(WED, 11), rules, set(), None, 7) == 15


def test_next_change(plan: RoomPlan) -> None:
    assert next_change(plan, ROOM, local(WED, 7), [], set(), None, 7) == (
        local(WED, 8),
        18,
    )
    assert next_change(plan, ROOM, local(WED, 22), [], set(), None, 7) == (
        local(WED + timedelta(1), 6),
        21,
    )


def test_next_change_skips_point_with_same_temp(plan: RoomPlan) -> None:
    plan.profiles["Praca"] = DayProfile.parse("06:00/21 08:00/21 22:00/17")
    assert next_change(plan, ROOM, local(WED, 7), [], set(), None, 7) == (
        local(WED, 22),
        17,
    )


def test_next_change_at_rule_start_and_end(plan: RoomPlan) -> None:
    rules = [CalendarRule(local(WED, 9), local(WED, 12), None, None, None, True)]
    assert next_change(plan, ROOM, local(WED, 8, 30), rules, set(), None, 7) == (
        local(WED, 9),
        None,
    )
    assert next_change(plan, ROOM, local(WED, 10), rules, set(), None, 7) == (
        local(WED, 12),
        18,
    )


def test_next_change_none_for_constant_plan() -> None:
    flat = RoomPlan(profiles={"Stała": DayProfile.parse("06:00/20")}, week=("Stała",) * 7)
    assert next_change(flat, ROOM, local(WED, 7), [], set(), None, 7) is None


def test_dst_wall_clock() -> None:
    sunday = date(2026, 3, 29)
    plan = RoomPlan(
        profiles={"Noc": DayProfile.parse("02:30/22 10:00/18")}, week=("Noc",) * 7
    )
    # 02:30 does not exist on that night; the clock goes from 02:00 to 03:00.
    assert local_datetime(sunday, 150) == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    before = datetime(2026, 3, 29, 0, 59, tzinfo=UTC)  # 01:59 CET
    assert target_at(plan, ROOM, before, [], set(), None, 7) == 18
    at_three = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)  # 03:00 CEST
    assert target_at(plan, ROOM, at_three, [], set(), None, 7) == 22
    assert next_change(plan, ROOM, before, [], set(), None, 7) == (at_three, 22)


def test_dst_end_uses_first_occurrence() -> None:
    sunday = date(2026, 10, 25)
    # 02:30 happens twice; the first one is 02:30 CEST = 00:30 UTC.
    assert local_datetime(sunday, 150) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_device_week_rolls_7_days(plan: RoomPlan) -> None:
    next_tuesday = WED + timedelta(days=6)
    rules = [day_rule(next_tuesday, profile="Wolne")]
    week = device_week(plan, ROOM, WED, rules, set(), None)
    assert sorted(week) == list(range(7))
    assert week[1] == FREE
    assert week[2] == WORK
    assert week[3] == WORK
    assert week[5] == FREE


def test_device_week_applies_min_temp(plan: RoomPlan) -> None:
    plan.profiles["Praca"] = DayProfile.parse("06:00/5 22:00/17")
    week = device_week(plan, ROOM, WED, [], set(), None, min_temp=7)
    assert week[2].to_z2m() == "06:00/7 22:00/17"
    assert week[5] == FREE


def test_rule_with_unknown_profile_does_not_hide_temp_rule(plan: RoomPlan) -> None:
    rules = [
        CalendarRule(local(WED, 10), local(WED, 12), None, None, 15, False),
        CalendarRule(local(WED), local(WED + timedelta(1)), ROOM, "Inny", None, False),
    ]
    assert target_at(plan, ROOM, local(WED, 11), rules, set(), None, 7) == 15


def test_same_scope_and_start_is_stable(plan: RoomPlan) -> None:
    short = CalendarRule(local(WED, 10), local(WED, 11), None, None, 15, False)
    long = CalendarRule(local(WED, 10), local(WED, 12), None, None, 22, False)
    for rules in ([short, long], [long, short]):
        assert target_at(plan, ROOM, local(WED, 10, 30), rules, set(), None, 7) == 22


def test_device_week_today_rule(plan: RoomPlan) -> None:
    rules = [day_rule(WED, profile="Wolne")]
    week = device_week(plan, ROOM, WED, rules, set(), None)
    assert week[2] == FREE
