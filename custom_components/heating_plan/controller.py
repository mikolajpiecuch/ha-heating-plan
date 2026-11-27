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
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .calendar_rules import CalendarRule, parse_event
from .const import HOLIDAY_COUNTRY, Options, RoomConfig
from .model import (
    MAX_TEMP,
    DayProfile,
    Override,
    OverrideKind,
    OverrideSource,
    PlanError,
    RoomPlan,
    round_temp,
)
from .resolver import (
    active_rule,
    device_week,
    local_datetime,
    next_change as resolver_next_change,
    target_at,
)
from .store import PlanStore, StoreData
from .trv import (
    DAYS,
    MODE_MANUAL,
    MODE_PAUSE,
    MODE_SCHEDULE,
    SOURCE_MANUAL,
    BoschTrv,
)

_LOGGER = logging.getLogger(__name__)

READBACK_TIMEOUT_PER_DAY = 30.0
"""Seconds to wait for the thermostat to report a written day."""

CALENDAR_INTERVAL = timedelta(minutes=15)
CALENDAR_DAYS = 8

OWN_WRITE_WINDOW = timedelta(seconds=60)
"""After an own write in this time, a setpoint report alone is not a knob turn:
an equal setpoint after an own setpoint write, and any setpoint after an own
change to schedule or pause (the thermostat then sets the setpoint itself)."""
KNOB_DELAY = 2.0
"""Seconds to wait after a knob report; then the state is read again."""

REMOTE_TEMP_INTERVAL = timedelta(minutes=15)
REMOTE_TEMP_MAX_AGE = timedelta(hours=1)
REMOTE_TEMP_CHANGE = 0.2

SyncState = Literal["synced", "pending", "error"]


def _clamp(temp: float, min_temp: float) -> float:
    return min(max(temp, min_temp), MAX_TEMP)


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
        self._apply_lock = asyncio.Lock()
        self._own_setpoint: tuple[float, datetime] | None = None
        self._own_mode_until: datetime | None = None
        self._knob_timer: CALLBACK_TYPE | None = None
        self._override_timer: CALLBACK_TYPE | None = None
        self._rule_timer: CALLBACK_TYPE | None = None
        self._last_pushed: float | None = None
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

    def plan_temp_now(self) -> float | None:
        """The plan target now without calendar temperature and pause rules."""
        home = self.home
        rules = [r for r in home.rules if r.profile is not None]
        return target_at(
            self.plan,
            self.name,
            dt_util.utcnow(),
            rules,
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
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.trv.source_entity_id], self._source_changed
            )
        )
        if self.room.sensor_entity_id is not None:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self.room.sensor_entity_id], self._sensor_changed
                )
            )
            # A report of the same value makes a stale value fresh again.
            self._unsubs.append(
                async_track_state_report_event(
                    self.hass, [self.room.sensor_entity_id], self._sensor_changed
                )
            )
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._remote_temp_interval, REMOTE_TEMP_INTERVAL
                )
            )
        override = self.override
        if override is not None and override.until is not None:
            if override.until <= dt_util.utcnow():
                # The override ended while Home Assistant was not running.
                del self.home.data["overrides"][self.room_id]
                await self.home.save()
        self._schedule_override_timer()
        self._unsubs.append(async_at_started(self.hass, self._started))

    async def stop(self) -> None:
        """Stop all listeners, timers and the running sync."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for timer in (self._override_timer, self._rule_timer, self._knob_timer):
            if timer is not None:
                timer()
        self._override_timer = None
        self._rule_timer = None
        self._knob_timer = None
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
        self._push_remote_temp(force=True)

    # --- room temperature -------------------------------------------------

    def _fresh_sensor_temp(self) -> float | None:
        """The room thermometer value, if it is valid and less than 1 h old."""
        if self.room.sensor_entity_id is None:
            return None
        state = self.hass.states.get(self.room.sensor_entity_id)
        if state is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if dt_util.utcnow() - state.last_reported > REMOTE_TEMP_MAX_AGE:
            return None
        return value

    @property
    def current_temp(self) -> float | None:
        """The room temperature: the room thermometer, else the thermostat."""
        value = self._fresh_sensor_temp()
        if value is not None:
            return value
        state = self.hass.states.get(self.trv.climate_entity_id)
        current = state.attributes.get("current_temperature") if state else None
        try:
            return float(current) if current is not None else None
        except (TypeError, ValueError):
            return None

    @callback
    def _sensor_changed(self, _event: Event[Any]) -> None:
        self._push_remote_temp(force=False)

    async def _remote_temp_interval(self, _now: datetime) -> None:
        self._push_remote_temp(force=True)

    @callback
    def _push_remote_temp(self, *, force: bool) -> None:
        """Send the room temperature: always (force) or on a change of 0.2 °C."""
        value = self._fresh_sensor_temp()
        if value is None:
            # The next valid value is sent at once.
            self._last_pushed = None
            return
        if not self.trv.available or not self.hass.is_running:
            return
        if not force and self._last_pushed is not None:
            if round(abs(value - self._last_pushed), 2) < REMOTE_TEMP_CHANGE:
                return
        self._last_pushed = value
        self.home.entry.async_create_background_task(
            self.hass, self._send_remote_temp(value), f"heating_plan temp {self.name}"
        )

    async def _send_remote_temp(self, value: float) -> None:
        try:
            await self.trv.push_remote_temp(value)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: cannot send the room temperature: %s", self.name, err)
            self._last_pushed = None

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
        elif is_online and was_online and old is not None and new is not None:
            self._setpoint_changed(old, new)

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
            today_rewritten = await self._sync()
        if self.trv.available:
            await self.apply(today_rewritten=today_rewritten)
        self._notify()

    async def _sync(self) -> bool:
        """Write and check the days. True if today was written in schedule mode."""
        if not self.trv.available:
            self.sync_state = "pending"
            return False
        today = dt_util.now().date()
        week = self.desired_week(today)
        texts = {d: p.to_z2m() for d, p in week.items()}
        days = self._mismatched_days(week)
        if days and self.sync_state == "error" and self._failed_week == texts:
            # The same write failed before. Wait for a new plan or a reconnect.
            return False
        today_written = today.weekday() in days
        was_schedule = self.trv.mode == MODE_SCHEDULE

        try:
            if days:
                self.sync_state = "pending"
                self._notify()
                if not await self._write_days(days):
                    self.sync_state = "pending"
                    return False
                days = await self._wait_for_days(days)
                if days:
                    _LOGGER.info(
                        "%s: the thermostat did not confirm %s, writing again",
                        self.name,
                        ", ".join(DAYS[d] for d in days),
                    )
                    if not await self._write_days(days):
                        self.sync_state = "pending"
                        return False
                    days = await self._wait_for_days(days)
        except HomeAssistantError as err:
            self._set_error(texts, f"Write failed: {err}")
            return False

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
            return False

        self.sync_state = "synced"
        self.last_error = None
        self._failed_week = None
        return today_written and was_schedule

    def _set_error(self, texts: dict[int, str], message: str) -> None:
        self.sync_state = "error"
        self.last_error = message
        self._failed_week = texts
        _LOGGER.warning("%s: %s", self.name, message)

    # --- mode and setpoint ----------------------------------------------

    async def apply(self, *, today_rewritten: bool = False) -> None:
        """Put the thermostat in the mode and setpoint that the room needs now.

        Priority: override -> calendar temperature or pause rule -> plan.
        """
        async with self._apply_lock:
            self._schedule_rule_timer()
            if self.trv.available:
                try:
                    await self._apply(today_rewritten)
                except HomeAssistantError as err:
                    self.last_error = f"Mode change failed: {err}"
                    _LOGGER.warning("%s: %s", self.name, self.last_error)
        self._notify()

    async def _apply(self, today_rewritten: bool) -> None:
        override = self.override
        if override is not None:
            await self._hold(override.temp)
            return
        rule = self.active_rule()
        if rule is not None:
            await self._hold(
                None
                if rule.off or rule.temp is None
                else _clamp(rule.temp, self.home.options.min_temp)
            )
            return
        if self.sync_state == "synced":
            target = self.target_now()
            if self.trv.mode != MODE_SCHEDULE:
                await self._set_mode(MODE_SCHEDULE)
                # The thermostat starts with the active point of its own plan.
                if target is not None and self._carry_over_differs(target):
                    await self._set_setpoint(target, force=True)
            elif today_rewritten or (
                target is not None and self._carry_over_differs(target)
            ):
                # A new value for the active point applies only from the next point.
                await self._set_setpoint(target)
            return
        # The plan on the thermostat is not confirmed: keep the plan target by hand.
        await self._hold(self.target_now())

    async def _hold(self, temp: float | None) -> None:
        """Manual mode with a setpoint, or pause for None."""
        if temp is None:
            if self.trv.mode != MODE_PAUSE:
                await self._set_mode(MODE_PAUSE)
            return
        if self.trv.mode != MODE_MANUAL:
            await self._set_mode(MODE_MANUAL)
        await self._set_setpoint(temp)

    async def _set_mode(self, mode: str) -> None:
        if mode in (MODE_SCHEDULE, MODE_PAUSE):
            self._own_mode_until = dt_util.utcnow() + OWN_WRITE_WINDOW
        await self.trv.set_mode(mode)

    async def _set_setpoint(self, temp: float | None, *, force: bool = False) -> None:
        if temp is None or (temp == self.trv.setpoint and not force):
            return
        self._own_setpoint = (temp, dt_util.utcnow() + OWN_WRITE_WINDOW)
        await self.trv.set_setpoint(temp)

    def active_rule(self) -> CalendarRule | None:
        """The calendar temperature or pause rule for the room now."""
        rule = active_rule(
            self.name, dt_util.utcnow(), self.home.rules, self.plan.profiles
        )
        if rule is None or (not rule.off and rule.temp is None):
            return None
        return rule

    @callback
    def _schedule_rule_timer(self) -> None:
        """Apply again at the next start or end of a temperature or pause rule."""
        if self._rule_timer is not None:
            self._rule_timer()
            self._rule_timer = None
        now = dt_util.utcnow()
        moments = [
            moment
            for rule in self.home.rules
            if (rule.off or rule.temp is not None)
            and (rule.room is None or rule.room == self.name)
            for moment in (rule.start, rule.end)
            if moment > now
        ]
        if self.override is None and self.sync_state != "synced":
            # The plan target is held by hand: apply again at the next plan change.
            change = self._plan_next_change(now)
            if change is not None:
                moments.append(change[0])
        if moments:
            self._rule_timer = async_track_point_in_utc_time(
                self.hass, self._rule_time, min(moments)
            )

    async def _rule_time(self, _now: datetime) -> None:
        self._rule_timer = None
        await self.apply()

    # --- overrides --------------------------------------------------------

    @property
    def override(self) -> Override | None:
        """The active override."""
        return self.home.data["overrides"].get(self.room_id)

    @property
    def target(self) -> float | None:
        """The target now, with the override (None = pause)."""
        override = self.override
        if override is not None:
            return override.temp
        return self.target_now()

    @property
    def next_change(self) -> tuple[datetime, float | None] | None:
        """The next change of the target: the end of the override, or the plan."""
        override = self.override
        if override is not None:
            if override.until is None:
                return None
            return override.until, self._plan_target(override.until)
        return self._plan_next_change(dt_util.utcnow())

    @property
    def mode(self) -> Literal["auto", "heat", "off"]:
        """auto = plan or timed override; heat and off = override until cancel."""
        override = self.override
        if override is None:
            return "auto"
        if override.temp is None:
            return "off"
        return "heat" if override.kind == "until_cancel" else "auto"

    def _plan_target(self, at: datetime) -> float | None:
        home = self.home
        return target_at(
            self.plan,
            self.name,
            at,
            home.rules,
            home.holidays,
            home.holiday_profile,
            home.options.min_temp,
        )

    def _plan_next_change(self, at: datetime) -> tuple[datetime, float | None] | None:
        home = self.home
        return resolver_next_change(
            self.plan,
            self.name,
            at,
            home.rules,
            home.holidays,
            home.holiday_profile,
            home.options.min_temp,
        )

    async def set_override(
        self,
        temp: float | None,
        kind: OverrideKind = "timed",
        hours: float | None = None,
        *,
        source: OverrideSource = "ha",
    ) -> None:
        """Hold a temperature (None = pause) for a time, to the next change or until cancel."""
        if temp is not None:
            temp = _clamp(round_temp(temp), self.home.options.min_temp)
        now = dt_util.utcnow()
        until: datetime | None = None
        if kind == "timed":
            duration = hours if hours is not None else self.room.override_hours
            until = now + timedelta(hours=duration)
        elif kind == "until_next":
            change = self._plan_next_change(now)
            until = change[0] if change else None
        self.home.data["overrides"][self.room_id] = Override(
            temp=temp, until=until, kind=kind, source=source
        )
        await self.home.save()
        self._schedule_override_timer()
        await self.apply()

    async def cancel_override(self) -> None:
        """End the override now."""
        if self.home.data["overrides"].pop(self.room_id, None) is not None:
            await self.home.save()
        self._schedule_override_timer()
        await self.apply()

    @callback
    def _schedule_override_timer(self) -> None:
        if self._override_timer is not None:
            self._override_timer()
            self._override_timer = None
        override = self.override
        if override is not None and override.until is not None:
            self._override_timer = async_track_point_in_utc_time(
                self.hass, self._override_time, override.until
            )

    async def _override_time(self, _now: datetime) -> None:
        self._override_timer = None
        override = self.override
        if override is None or override.until is None:
            return
        if override.until <= dt_util.utcnow():
            await self.cancel_override()
        else:
            self._schedule_override_timer()

    # --- knob -------------------------------------------------------------

    def _is_own_setpoint(self, temp: float) -> bool:
        if self._own_setpoint is None:
            return False
        value, until = self._own_setpoint
        return temp == value and dt_util.utcnow() < until

    @callback
    def _source_changed(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if new is None or new.state != SOURCE_MANUAL:
            return
        if old is None or old.state in (SOURCE_MANUAL, STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        self._schedule_knob_check()

    @callback
    def _setpoint_changed(self, old: State, new: State) -> None:
        if old.attributes.get(ATTR_TEMPERATURE) == new.attributes.get(ATTR_TEMPERATURE):
            return
        if self._own_mode_until is not None and dt_util.utcnow() < self._own_mode_until:
            # After an own change to schedule or pause the thermostat sets the
            # setpoint itself; the change source can still be an old "manual".
            return
        if self.trv.change_source != SOURCE_MANUAL:
            return
        self._schedule_knob_check()

    @callback
    def _schedule_knob_check(self) -> None:
        """Decide later: the setpoint and the source can come in two reports."""
        if self._knob_timer is not None:
            self._knob_timer()
        self._knob_timer = async_call_later(self.hass, KNOB_DELAY, self._knob_check)

    @callback
    def _knob_check(self, _now: datetime) -> None:
        """The knob was turned: a timed override with the new temperature."""
        self._knob_timer = None
        if not self.hass.is_running or self.trv.change_source != SOURCE_MANUAL:
            return
        temp = self.trv.setpoint
        if temp is None or self._is_own_setpoint(temp):
            return
        override = self.override
        if override is not None and override.source == "knob" and override.temp == temp:
            return
        _LOGGER.debug("%s: the knob changed the setpoint to %s", self.name, temp)
        self.home.entry.async_create_background_task(
            self.hass,
            self.set_override(temp, "timed", source="knob"),
            f"heating_plan knob {self.name}",
        )

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

    async def cancel_all(self) -> None:
        """End the overrides of all rooms."""
        for room in self.rooms.values():
            await room.cancel_override()

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
