"""Tests for the plan model and the Zigbee2MQTT day format."""

from datetime import UTC, datetime

import pytest

from custom_components.heating_plan.model import (
    DayProfile,
    Override,
    PlanError,
    Point,
    RoomPlan,
)


def test_parse_and_format_roundtrip() -> None:
    p = DayProfile.parse("22:00/17 6:00/21,5")
    assert p.to_z2m() == "06:00/21.5 22:00/17"
    assert p.points == (Point(360, 21.5), Point(1320, 17.0))


def test_empty_day_rejected() -> None:
    with pytest.raises(PlanError):
        DayProfile.parse("")
    with pytest.raises(PlanError):
        DayProfile.parse("   ")
    with pytest.raises(PlanError):
        DayProfile(())


def test_ten_points_accepted() -> None:
    text = " ".join(f"{h:02d}:00/20" for h in range(10))
    assert len(DayProfile.parse(text).points) == 10


def test_eleven_points_rejected() -> None:
    text = " ".join(f"{h:02d}:00/20" for h in range(11))
    with pytest.raises(PlanError):
        DayProfile.parse(text)


def test_duplicate_time_rejected() -> None:
    with pytest.raises(PlanError):
        DayProfile.parse("06:00/21 06:00/20")


def test_temp_out_of_range() -> None:
    with pytest.raises(PlanError):
        DayProfile.parse("06:00/4.9")
    with pytest.raises(PlanError):
        DayProfile.parse("06:00/30.1")
    assert DayProfile.parse("06:00/5 07:00/30").to_z2m() == "06:00/5 07:00/30"


def test_temp_rounded_to_tenth() -> None:
    assert DayProfile.parse("06:00/21.25").points[0].temp == 21.2
    assert DayProfile.parse("06:00/21.29").points[0].temp == 21.2
    assert DayProfile.parse("06:00/5.0").to_z2m() == "06:00/5"


@pytest.mark.parametrize(
    "text",
    ["24:00/20", "06:60/20", "0600/20", "06:00", "06:00/abc", "6/20", "06:00/20/1"],
)
def test_invalid_syntax_rejected(text: str) -> None:
    with pytest.raises(PlanError):
        DayProfile.parse(text)


def test_point_cuts_temp_to_tenth() -> None:
    point = Point(0, 21.29)
    assert point.temp == 21.2
    assert DayProfile((point,)).to_z2m() == "00:00/21.2"


def test_non_ascii_digits_rejected() -> None:
    with pytest.raises(PlanError):
        DayProfile.parse("\u0660\u0666:\u0660\u0660/\u0662\u0661")


def test_temp_at_minute_out_of_range() -> None:
    p = DayProfile.parse("06:00/21")
    with pytest.raises(PlanError):
        p.temp_at(1440, previous_day=p)
    with pytest.raises(PlanError):
        p.temp_at(-1, previous_day=p)


def test_temp_at() -> None:
    p = DayProfile.parse("06:00/21 08:00/18 16:00/21 22:00/17")
    assert p.temp_at(6 * 60, previous_day=p) == 21
    assert p.temp_at(7 * 60 + 59, previous_day=p) == 21
    assert p.temp_at(8 * 60, previous_day=p) == 18
    assert p.temp_at(23 * 60, previous_day=p) == 17


def test_temp_before_first_point_uses_previous_day() -> None:
    mon = DayProfile.parse("06:00/21 22:00/17")
    tue = DayProfile.parse("07:00/20")
    assert tue.temp_at(5 * 60, previous_day=mon) == 17


def _plan() -> RoomPlan:
    return RoomPlan(
        profiles={
            "Praca": DayProfile.parse("06:00/21 08:00/18 16:00/21 22:00/17"),
            "Wolne": DayProfile.parse("07:30/21 23:00/17"),
        },
        week=("Praca",) * 5 + ("Wolne",) * 2,
    )


def test_valid_plan() -> None:
    _plan().validate()


def test_week_must_reference_existing_profiles() -> None:
    plan = _plan()
    plan.week = ("Praca",) * 4 + ("Brak",) + ("Wolne",) * 2
    with pytest.raises(PlanError):
        plan.validate()


def test_week_must_have_seven_days() -> None:
    plan = _plan()
    plan.week = ("Praca",) * 6
    with pytest.raises(PlanError):
        plan.validate()


def test_plan_needs_a_profile_name() -> None:
    plan = _plan()
    plan.profiles[""] = DayProfile.parse("06:00/20")
    with pytest.raises(PlanError):
        plan.validate()


def test_override_holds_values() -> None:
    until = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    o = Override(temp=None, until=until, kind="timed", source="ha")
    assert o.temp is None
    assert o.until == until
