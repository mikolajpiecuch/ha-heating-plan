"""Tests for parsing calendar events into rules."""

from datetime import UTC, datetime

from custom_components.heating_plan.calendar_rules import CalendarRule, parse_event

ROOMS = ["Salon", "Sypialnia", "Biuro", "Pokój gościnny"]
PROFILES = ["Praca", "Wolne"]
S = datetime(2026, 12, 2, 0, 0, tzinfo=UTC)
E = datetime(2026, 12, 3, 0, 0, tzinfo=UTC)


def parse(summary: str) -> CalendarRule | None:
    return parse_event(summary, S, E, ROOMS, PROFILES)


def test_profile_whole_house() -> None:
    assert parse("Wolne") == CalendarRule(S, E, None, "Wolne", None, False)


def test_profile_case_insensitive() -> None:
    assert parse("dzień WOLNE") == CalendarRule(S, E, None, "Wolne", None, False)


def test_room_case_and_diacritics() -> None:
    rule = parse("sypialnia wolne")
    assert rule is not None
    assert rule.room == "Sypialnia"
    assert rule.profile == "Wolne"


def test_room_with_two_words_and_diacritics() -> None:
    rule = parse("pokoj goscinny 19")
    assert rule is not None
    assert (rule.room, rule.temp) == ("Pokój gościnny", 19)


def test_temp_with_free_text() -> None:
    rule = parse("Urlop 15")
    assert rule is not None
    assert rule.temp == 15
    assert rule.room is None
    assert rule.profile is None
    assert not rule.off


def test_temp_with_decimal_comma() -> None:
    rule = parse("Urlop 15,5")
    assert rule is not None
    assert rule.temp == 15.5


def test_room_temp() -> None:
    rule = parse("Salon 22")
    assert rule is not None
    assert (rule.room, rule.temp) == ("Salon", 22)


def test_off() -> None:
    rule = parse("Biuro wyłącz")
    assert rule is not None
    assert rule.off
    assert rule.room == "Biuro"
    assert rule.temp is None
    assert rule.profile is None
    other = parse("wylacz")
    assert other is not None
    assert other.off
    assert other.room is None


def test_unrelated_event_ignored() -> None:
    assert parse("Dentysta") is None
    assert parse("Dentysta 15:00") is None
    assert parse("Wizyta 24.12") is None
    assert parse("Salon") is None
    assert parse("") is None


def test_room_name_inside_a_word_is_not_a_room() -> None:
    rule = parse("Salonik 20")
    assert rule is not None
    assert rule.room is None


def test_profile_wins_over_number() -> None:
    rule = parse("Wolne 2 dni")
    assert rule is not None
    assert rule.profile == "Wolne"
    assert rule.temp is None
