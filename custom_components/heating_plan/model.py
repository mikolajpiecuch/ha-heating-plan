"""Plan model and the Zigbee2MQTT day format.

This module does not import Home Assistant, so that the tests are fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
import re
from typing import Literal

MIN_POINTS = 1
MAX_POINTS = 10
MIN_TEMP = 5.0
MAX_TEMP = 30.0
DAYS_IN_WEEK = 7
MINUTES_IN_DAY = 24 * 60

_POINT_RE = re.compile(r"^(\d{1,2}):(\d{2})/(\d{1,2}(?:[.,]\d+)?)$", re.ASCII)

OverrideKind = Literal["timed", "until_next", "until_cancel"]
OverrideSource = Literal["ha", "knob"]


class PlanError(ValueError):
    """A plan or a day profile is not valid."""


def round_temp(temp: float) -> float:
    """Cut a temperature to 0.1 °C, as the thermostat does (21.25 -> 21.2)."""
    try:
        value = Decimal(str(temp)).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
    except InvalidOperation as err:
        raise PlanError(f"Temperature {temp} is not a number") from err
    return float(value)


def format_temp(temp: float) -> str:
    """Format a temperature as Zigbee2MQTT does: 21, 21.5."""
    text = f"{temp:.1f}"
    return text[:-2] if text.endswith(".0") else text


@dataclass(frozen=True)
class Point:
    """One change of the target temperature in a day."""

    minute: int  # minute of the day, 0-1439
    temp: float

    def __post_init__(self) -> None:
        # Cut to 0.1 °C here too, so that every point agrees with its Z2M text.
        object.__setattr__(self, "temp", round_temp(self.temp))
        if not 0 <= self.minute < MINUTES_IN_DAY:
            raise PlanError(f"Time {self.minute} is not in 00:00-23:59")
        if not MIN_TEMP <= self.temp <= MAX_TEMP:
            raise PlanError(
                f"Temperature {format_temp(self.temp)} is not in "
                f"{format_temp(MIN_TEMP)}-{format_temp(MAX_TEMP)} °C"
            )

    @property
    def time_text(self) -> str:
        """The time as HH:MM."""
        return f"{self.minute // 60:02d}:{self.minute % 60:02d}"


@dataclass(frozen=True)
class DayProfile:
    """A named plan of one day: 1-10 points, sorted by time."""

    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        points = tuple(sorted(self.points, key=lambda p: p.minute))
        if len(points) < MIN_POINTS:
            raise PlanError("A day must have at least one point")
        if len(points) > MAX_POINTS:
            raise PlanError(f"A day can have at most {MAX_POINTS} points")
        minutes = [p.minute for p in points]
        if len(set(minutes)) != len(minutes):
            raise PlanError("Two points in a day have the same time")
        object.__setattr__(self, "points", points)

    @classmethod
    def parse(cls, text: str) -> DayProfile:
        """Parse the Zigbee2MQTT format, e.g. ``06:00/21 22:00/17.5``.

        Unsorted points, ``H:MM`` and a decimal comma are accepted.
        """
        points: list[Point] = []
        for item in text.split():
            match = _POINT_RE.match(item)
            if match is None:
                raise PlanError(f"'{item}' is not in the format HH:MM/temperature")
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                raise PlanError(f"'{item}' has a time that is not in 00:00-23:59")
            temp = round_temp(float(match.group(3).replace(",", ".")))
            points.append(Point(hour * 60 + minute, temp))
        return cls(tuple(points))

    def to_z2m(self) -> str:
        """Format the day for Zigbee2MQTT."""
        return " ".join(f"{p.time_text}/{format_temp(p.temp)}" for p in self.points)

    def temp_at(self, minute: int, previous_day: DayProfile) -> float:
        """The target at a minute of the day.

        Before the first point, the last point of the previous day applies.
        """
        if not 0 <= minute < MINUTES_IN_DAY:
            raise PlanError(f"Minute {minute} is not in 0-{MINUTES_IN_DAY - 1}")
        temp = previous_day.points[-1].temp
        for point in self.points:
            if point.minute > minute:
                break
            temp = point.temp
        return temp


@dataclass
class RoomPlan:
    """Named day profiles and the profile for each weekday (Monday = 0)."""

    profiles: dict[str, DayProfile]
    week: tuple[str, ...]

    def validate(self) -> None:
        """Raise PlanError if the plan is not valid."""
        if not self.profiles:
            raise PlanError("A plan must have at least one profile")
        for name, profile in self.profiles.items():
            if not isinstance(name, str) or not name.strip():
                raise PlanError("A profile must have a name")
            if not isinstance(profile, DayProfile):
                raise PlanError(f"Profile '{name}' is not a day profile")
        if len(self.week) != DAYS_IN_WEEK:
            raise PlanError(f"A week must have {DAYS_IN_WEEK} days")
        for name in self.week:
            if name not in self.profiles:
                raise PlanError(f"Profile '{name}' does not exist")


@dataclass
class Override:
    """A manual target for a period. ``temp=None`` is pause."""

    temp: float | None
    until: datetime | None
    kind: OverrideKind
    source: OverrideSource
