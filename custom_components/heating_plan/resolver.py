"""Find the profile and the target temperature for a room at a time.

Priority: calendar rule (room before whole home, then the later start)
-> public holiday (if a holiday profile is set) -> weekly plan.
All times use the Home Assistant time zone (``dt_util``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

from .calendar_rules import CalendarRule
from .model import MAX_TEMP, DayProfile, RoomPlan

NEXT_CHANGE_DAYS = 8


def local_datetime(day: date, minute: int) -> datetime:
    """The first moment at which the wall clock shows a minute of a day.

    A time that does not exist (the clock goes forward) gives the moment of
    the change. A time that occurs twice (the clock goes back) gives the first one.
    """
    tz = dt_util.get_default_time_zone()
    wall = datetime.combine(day, time(minute // 60, minute % 60))
    first = wall.replace(tzinfo=tz, fold=0).astimezone(UTC)
    if first.astimezone(tz).replace(tzinfo=None) == wall:
        return first
    # The time does not exist: find the moment the clock jumps over it.
    moment = wall.replace(tzinfo=tz, fold=1).astimezone(UTC)
    while moment.astimezone(tz).replace(tzinfo=None) < wall:
        moment += timedelta(minutes=1)
    return moment


def _wall(at: datetime) -> tuple[date, int]:
    local = dt_util.as_local(at)
    return local.date(), local.hour * 60 + local.minute


def _applies_to(rule: CalendarRule, room: str) -> bool:
    return rule.room is None or rule.room == room


def _best(rules: list[CalendarRule]) -> CalendarRule | None:
    """Room rule before a whole-home rule; then the later start."""
    if not rules:
        return None
    return max(rules, key=lambda r: (r.room is not None, r.start))


def profile_for_date(
    plan: RoomPlan,
    room: str,
    day: date,
    rules: list[CalendarRule],
    holidays: set[date],
    holiday_profile: str | None,
) -> str:
    """The name of the profile for a room on a day."""
    start = local_datetime(day, 0)
    end = local_datetime(day + timedelta(days=1), 0)
    rule = _best(
        [
            r
            for r in rules
            if r.profile is not None
            and r.profile in plan.profiles
            and _applies_to(r, room)
            and r.overlaps(start, end)
        ]
    )
    if rule is not None and rule.profile is not None:
        return rule.profile
    if day in holidays and holiday_profile in plan.profiles:
        return holiday_profile  # type: ignore[return-value]
    return plan.week[day.weekday()]


def _profile(
    plan: RoomPlan,
    room: str,
    day: date,
    rules: list[CalendarRule],
    holidays: set[date],
    holiday_profile: str | None,
) -> DayProfile:
    return plan.profiles[
        profile_for_date(plan, room, day, rules, holidays, holiday_profile)
    ]


def _clamp(temp: float, min_temp: float) -> float:
    return min(max(temp, min_temp), MAX_TEMP)


def target_at(
    plan: RoomPlan,
    room: str,
    at: datetime,
    rules: list[CalendarRule],
    holidays: set[date],
    holiday_profile: str | None,
    min_temp: float,
) -> float | None:
    """The target temperature at a time. None means pause."""
    rule = _best([r for r in rules if _applies_to(r, room) and r.active_at(at)])
    if rule is not None and rule.off:
        return None
    if rule is not None and rule.temp is not None:
        return _clamp(rule.temp, min_temp)

    day, minute = _wall(at)
    today = _profile(plan, room, day, rules, holidays, holiday_profile)
    yesterday = _profile(
        plan, room, day - timedelta(days=1), rules, holidays, holiday_profile
    )
    return _clamp(today.temp_at(minute, previous_day=yesterday), min_temp)


def next_change(
    plan: RoomPlan,
    room: str,
    at: datetime,
    rules: list[CalendarRule],
    holidays: set[date],
    holiday_profile: str | None,
    min_temp: float,
) -> tuple[datetime, float | None] | None:
    """The next time the target changes, and the new target (None = pause)."""
    current = target_at(plan, room, at, rules, holidays, holiday_profile, min_temp)
    first_day, _ = _wall(at)
    moments: set[datetime] = set()
    for offset in range(NEXT_CHANGE_DAYS + 1):
        day = first_day + timedelta(days=offset)
        moments.add(local_datetime(day, 0))
        profile = _profile(plan, room, day, rules, holidays, holiday_profile)
        moments.update(local_datetime(day, p.minute) for p in profile.points)
    for rule in rules:
        if _applies_to(rule, room):
            moments.update((rule.start, rule.end))

    horizon = at + timedelta(days=NEXT_CHANGE_DAYS)
    for moment in sorted(m.astimezone(UTC) for m in moments):
        if moment <= at or moment > horizon:
            continue
        temp = target_at(
            plan, room, moment, rules, holidays, holiday_profile, min_temp
        )
        if temp != current:
            return moment, temp
    return None


def device_week(
    plan: RoomPlan,
    room: str,
    today: date,
    rules: list[CalendarRule],
    holidays: set[date],
    holiday_profile: str | None,
) -> dict[int, DayProfile]:
    """The profile for each weekday on the thermostat: today to today + 6."""
    week: dict[int, DayProfile] = {}
    for offset in range(7):
        day = today + timedelta(days=offset)
        week[day.weekday()] = _profile(
            plan, room, day, rules, holidays, holiday_profile
        )
    return week
