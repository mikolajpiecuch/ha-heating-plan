"""Room and home controllers.

A RoomController keeps the effective plan of the next 7 days on its
thermostat and puts the thermostat in ``schedule`` mode only when all 7 days
on the thermostat are equal to the plan in Home Assistant.

The HomeController reads the calendar and the public holidays and keeps the
stored data of all rooms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import Any, Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .calendar_rules import CalendarRule, parse_event
from .const import HOLIDAY_COUNTRY, Options, RoomConfig
from .model import DayProfile, PlanError, RoomPlan
from .resolver import device_week, local_datetime, target_at
from .store import PlanStore, StoreData
from .trv import DAYS, MODE_SCHEDULE, BoschTrv

_LOGGER = logging.getLogger(__name__)

READBACK_TIMEOUT_PER_DAY = 30.0
"""Seconds to wait for the thermostat to report a written day."""

CALENDAR_INTERVAL = timedelta(minutes=15)
CALENDAR_DAYS = 8

SyncState = Literal["synced", "pending", "error"]


def _same_day(text: str, profile: DayProfile) -> bool:
    """True if the day text from the thermostat is equal to the profile."""
    try:
        return DayProfile.parse(text) == profile
    except PlanError:
        return False


def _parse_time(value: str) -> datetime:
    """A calendar time: a date (all day, local midnight) or a date and time."""
    if "T" not in value and " " not in value:
        return local_datetime(date.fromisoformat(value), 0)
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"'{value}' is not a time")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(parsed)


class RoomController:
    """Keep one room and its thermostat in line with the plan."""

    def __init__(
        self,
        hass: HomeAssistant,
        room_id: str,
        room: RoomConfig,
        trv: BoschTrv,
        store: PlanStore,
        home: HomeController,
    ) -> None:
        self.hass = hass
        self.room_id = room_id
        self.room = room
        self.trv = trv
        self.store = store
        self.home = home
        self.sync_state: SyncState = "pending"
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._sync_again = False
        self._sync_task: asyncio.Task | None = None
        self._failed_week: dict[int, str] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    # --- data -------------------------------------------------------------

    @property
    def name(self) -> str:
        """The room name."""
        return self.room.name

    @property
    def plan(self) -> RoomPlan:
        """The plan of the room."""
        return self.home.data["plans"][self.room_id]

    def desired_week(self, today: date | None = None) -> dict[int, DayProfile]:
        """The profile for each weekday on the thermostat, from today."""
        home = self.home
        return device_week(
            self.plan,
            self.name,
            today or dt_util.now().date(),
            home.rules,
            home.holidays,
            home.holiday_profile,
            home.options.min_temp,
        )

    def target_now(self) -> float | None:
        """The target of the plan and the calendar now (None = pause)."""
        home = self.home
        return target_at(
            self.plan,
            self.name,
            dt_util.utcnow(),
            home.rules,
            home.holidays,
            home.holiday_profile,
            home.options.min_temp,
        )

    # --- life cycle -------------------------------------------------------

    async def start(self) -> None:
        """Start to follow the thermostat. The first sync runs when HA has started."""
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.trv.climate_entity_id], self._climate_changed
            )
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, self.trv.day_entity_ids, self._day_changed
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._started))

    async def stop(self) -> None:
        """Stop all listeners and the running sync."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call a function when the state of the room changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _started(self, _hass: HomeAssistant) -> None:
        self.request_sync()

    @callback
    def _climate_changed(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        was_online = old is not None and old.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        is_online = new is not None and new.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        if is_online and not was_online:
            self.request_sync(force=True)
        elif was_online and not is_online:
            self.sync_state = "pending"
            self._notify()

    @callback
    def _day_changed(self, event: Event[EventStateChangedData]) -> None:
        """The thermostat reported a day. Sync if it is not the plan."""
        if self._lock.locked() or not self.hass.is_running:
            return
        if self._mismatched_days(self.desired_week()):
            self.request_sync()

    # --- sync -------------------------------------------------------------

    @callback
    def request_sync(self, *, force: bool = False) -> None:
        """Run a sync in the background. A running sync runs once more after it."""
        if force:
            self._failed_week = None
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_again = True
            return
        self._sync_task = self.home.entry.async_create_background_task(
            self.hass, self._sync_loop(), f"heating_plan sync {self.name}"
        )

    async def _sync_loop(self) -> None:
        while True:
            self._sync_again = False
            await self.sync()
            if not self._sync_again:
                return

    def _mismatched_days(self, week: dict[int, DayProfile]) -> dict[int, DayProfile]:
        return {
            weekday: profile
            for weekday, profile in week.items()
            if not _same_day(self.trv.day_text(weekday), profile)
        }

    async def _wait_for_days(self, days: dict[int, DayProfile]) -> dict[int, DayProfile]:
        """Wait until the thermostat reports the days. Return the days that differ."""
        done = asyncio.Event()

        @callback
        def check(_event: Event[EventStateChangedData] | None = None) -> None:
            if not self._mismatched_days(days):
                done.set()

        unsub = async_track_state_change_event(
            self.hass, [self.trv.day_entity_ids[d] for d in days], check
        )
        timeout = READBACK_TIMEOUT_PER_DAY * len(days)
        waiter = asyncio.ensure_future(done.wait())
        try:
            check()
            if timeout > 0:
                # asyncio.wait does not cancel this task when the time is over.
                await asyncio.wait({waiter}, timeout=timeout)
        finally:
            waiter.cancel()
            unsub()
        return self._mismatched_days(days)

    async def _write_days(self, days: dict[int, DayProfile]) -> bool:
        """Write days one after the other. False if the thermostat went offline."""
        for weekday, profile in days.items():
            if not self.trv.available:
                return False
            await self.trv.write_day(weekday, profile)
        return True

    async def sync(self) -> None:
        """Write the days that differ, check them and set the mode."""
        async with self._lock:
            await self._sync()
        self._notify()

    async def _sync(self) -> None:
        if not self.trv.available:
            self.sync_state = "pending"
            return
        today = dt_util.now().date()
        week = self.desired_week(today)
        texts = {d: p.to_z2m() for d, p in week.items()}
        days = self._mismatched_days(week)
        if days and self.sync_state == "error" and self._failed_week == texts:
            # The same write failed before. Wait for a new plan or a reconnect.
            return
        today_written = today.weekday() in days
        was_schedule = self.trv.mode == MODE_SCHEDULE

        try:
            if days:
                self.sync_state = "pending"
                self._notify()
                if not await self._write_days(days):
                    self.sync_state = "pending"
                    return
                days = await self._wait_for_days(days)
                if days:
                    _LOGGER.info(
                        "%s: the thermostat did not confirm %s, writing again",
                        self.name,
                        ", ".join(DAYS[d] for d in days),
                    )
                    if not await self._write_days(days):
                        self.sync_state = "pending"
                        return
                    days = await self._wait_for_days(days)
        except HomeAssistantError as err:
            self._set_error(texts, f"Write failed: {err}")
            return

        if not days and self._mismatched_days(week):
            # Another day changed on the thermostat in the meantime.
            days = self._mismatched_days(week)
        if days:
            self._set_error(
                texts,
                "The thermostat did not confirm "
                + ", ".join(
                    f"{DAYS[d]} (sent '{p.to_z2m()}', has '{self.trv.day_text(d)}')"
                    for d, p in days.items()
                ),
            )
            return

        self.sync_state = "synced"
        self.last_error = None
        self._failed_week = None
        try:
            await self._apply(today_written and was_schedule)
        except HomeAssistantError as err:
            self.last_error = f"Mode change failed: {err}"
            _LOGGER.warning("%s: %s", self.name, self.last_error)

    def _set_error(self, texts: dict[int, str], message: str) -> None:
        self.sync_state = "error"
        self.last_error = message
        self._failed_week = texts
        _LOGGER.warning("%s: %s", self.name, message)

    async def _apply(self, today_rewritten: bool) -> None:
        """Put the thermostat in the mode that the plan needs."""
        target = self.target_now()
        if self.trv.mode != MODE_SCHEDULE:
            await self.trv.set_mode(MODE_SCHEDULE)
            # The thermostat starts with the active point of its own plan.
            if target is not None and self._carry_over_differs(target):
                await self.trv.set_setpoint(target)
        elif today_rewritten or (
            target is not None and self._carry_over_differs(target)
        ):
            # A new value for the active point applies only from the next point.
            if target is not None and target != self.trv.setpoint:
                await self.trv.set_setpoint(target)

    def _carry_over_differs(self, target: float) -> bool:
        """Before today's first point the thermostat uses the last point of the
        weekday slot of yesterday. That slot can already hold next week's profile.
        True if its temperature is not the target now.
        """
        now = dt_util.now()
        week = self.desired_week(now.date())
        first = week[now.weekday()].points[0].minute
        if now.hour * 60 + now.minute >= first:
            return False
        carried = week[(now.weekday() - 1) % 7].points[-1].temp
        return carried != target

    async def set_plan(self, plan: RoomPlan) -> None:
        """Check, save and write a new plan."""
        plan.validate()
        self.home.data["plans"][self.room_id] = plan
        await self.home.save()
        self.request_sync(force=True)
        self._notify()


class HomeController:
    """Calendar, holidays and stored data for all rooms."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: PlanStore,
        data: StoreData,
        options: Options,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.data = data
        self.options = options
        self.rules: list[CalendarRule] = []
        self.holidays: set[date] = set()
        self.rooms: dict[str, RoomController] = {}
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def holiday_profile(self) -> str | None:
        """The profile for public holidays, or None if holidays are off."""
        return self.options.holiday_profile if self.options.holidays_enabled else None

    async def save(self) -> None:
        """Save the plans and the overrides."""
        await self.store.save(self.data)

    async def start(self) -> None:
        """Read the holidays and the calendar, then start the rooms."""
        await self.refresh_holidays()
        await self.refresh_calendar(sync=False)
        for room in self.rooms.values():
            await room.start()
        self._unsubs.append(
            async_track_time_interval(self.hass, self._interval, CALENDAR_INTERVAL)
        )
        self._unsubs.append(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=5, second=0)
        )

    async def stop(self) -> None:
        """Stop the timers and the rooms."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for room in self.rooms.values():
            await room.stop()

    async def _interval(self, _now: datetime) -> None:
        await self.refresh_calendar()

    async def _midnight(self, _now: datetime) -> None:
        """A new day: the day that passed gets the profile for next week."""
        await self.refresh_holidays()
        await self.refresh_calendar(sync=False)
        for room in self.rooms.values():
            room.request_sync()

    async def refresh_holidays(self) -> None:
        """Read the public holidays of this year and the next year."""
        if not self.options.holidays_enabled:
            self.holidays = set()
            return
        year = dt_util.now().year

        def load() -> set[date]:
            import holidays  # noqa: PLC0415

            return set(holidays.country_holidays(HOLIDAY_COUNTRY, years=[year, year + 1]))

        self.holidays = await self.hass.async_add_executor_job(load)

    async def refresh_calendar(self, *, sync: bool = True) -> None:
        """Read the events of the next days. Sync the rooms if the rules changed."""
        rules = await self._read_calendar()
        if rules is None or rules == self.rules:
            return
        self.rules = rules
        if sync:
            for room in self.rooms.values():
                room.request_sync()

    async def _read_calendar(self) -> list[CalendarRule] | None:
        calendar = self.options.calendar_entity_id
        if calendar is None:
            return []
        start = local_datetime(dt_util.now().date() - timedelta(days=1), 0)
        end = start + timedelta(days=CALENDAR_DAYS + 1)
        try:
            response: Any = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendar,
                    "start_date_time": start.isoformat(),
                    "end_date_time": end.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Cannot read the calendar %s: %s", calendar, err)
            return None
        events = (response or {}).get(calendar, {}).get("events", [])
        rooms = [room.name for room in self.rooms.values()]
        profiles = sorted({name for p in self.data["plans"].values() for name in p.profiles})
        rules: list[CalendarRule] = []
        for event in events:
            try:
                rule = parse_event(
                    event.get("summary", ""),
                    _parse_time(event["start"]),
                    _parse_time(event["end"]),
                    rooms,
                    profiles,
                )
            except (KeyError, ValueError) as err:
                _LOGGER.debug("Calendar event ignored: %s (%s)", event, err)
                continue
            if rule is not None:
                rules.append(rule)
        rules.sort(key=lambda r: (r.start, r.end))
        return rules
