"""Calendar events as heating rules.

Event titles (case and Polish diacritics do not matter):

- ``Wolne`` - the whole home uses the profile "Wolne" on the days of the event.
- ``Sypialnia Wolne`` - only one room.
- ``15``, ``Urlop 15`` - the whole home keeps 15 °C for the time of the event.
- ``Salon 22`` - one room keeps 22 °C.
- ``wyłącz``, ``Biuro wyłącz`` - pause.

Other events are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

_PL = str.maketrans("ąćęłńóśźż", "acelnoszz")
_OFF_WORDS = ("wylacz",)
_NUMBER_RE = re.compile(r"(?<![\w.,:/])(\d{1,2}(?:[.,]\d)?)(?![\w.,:/])")


@dataclass(frozen=True)
class CalendarRule:
    """One rule from a calendar event. Exactly one of profile, temp and off is set."""

    start: datetime
    end: datetime
    room: str | None  # None = the whole home
    profile: str | None
    temp: float | None
    off: bool

    def active_at(self, at: datetime) -> bool:
        """Return True if the rule is in effect at a time."""
        return self.start <= at < self.end

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Return True if the rule is in effect at some time in [start, end)."""
        return self.start < end and self.end > start


def normalize(text: str) -> str:
    """Lower case, no Polish diacritics, single spaces."""
    return " ".join(text.casefold().translate(_PL).split())


def _find(text: str, names: list[str]) -> str | None:
    """Return the name that is in the text as whole words (the longest one)."""
    found: str | None = None
    for name in names:
        key = normalize(name)
        if not key:
            continue
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text) and (
            found is None or len(key) > len(normalize(found))
        ):
            found = name
    return found


def parse_event(
    summary: str,
    start: datetime,
    end: datetime,
    rooms: list[str],
    profiles: list[str],
) -> CalendarRule | None:
    """Make a rule from a calendar event, or None if the event is not a rule."""
    text = normalize(summary)
    if not text:
        return None
    room = _find(text, rooms)

    if any(re.search(rf"(?<!\w){word}(?!\w)", text) for word in _OFF_WORDS):
        return CalendarRule(start, end, room, None, None, True)

    profile = _find(text, profiles)
    if profile is not None:
        return CalendarRule(start, end, room, profile, None, False)

    match = _NUMBER_RE.search(text)
    if match is not None:
        temp = float(match.group(1).replace(",", "."))
        return CalendarRule(start, end, room, None, temp, False)

    return None
