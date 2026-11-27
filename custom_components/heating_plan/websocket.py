"""Websocket commands for the Lovelace card."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .controller import RoomController
from .model import PlanError
from .resolver import profile_for_date
from .services import climate_entity_id, homes, parse_plan, plan_to_dict, room_for_entity


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the commands."""
    websocket_api.async_register_command(hass, ws_rooms)
    websocket_api.async_register_command(hass, ws_set_plan)


def room_to_dict(hass: HomeAssistant, room: RoomController) -> dict[str, Any]:
    """All the data that the card shows for a room."""
    home = room.home
    today = dt_util.now().date()
    today_name = profile_for_date(
        room.plan, room.name, today, home.rules, home.holidays, home.holiday_profile
    )
    yesterday_name = profile_for_date(
        room.plan,
        room.name,
        today - timedelta(days=1),
        home.rules,
        home.holidays,
        home.holiday_profile,
    )
    today_profile = room.plan.profiles[today_name]
    override = room.override
    change = room.next_change
    return {
        "entity_id": climate_entity_id(hass, room),
        "room_id": room.room_id,
        "name": room.name,
        "area_id": room.room.area_id,
        "plan": plan_to_dict(room.plan),
        "override": (
            {
                "temp": override.temp,
                "until": override.until.isoformat() if override.until else None,
                "kind": override.kind,
                "source": override.source,
            }
            if override
            else None
        ),
        "mode": room.mode,
        "target": room.target,
        "current_temp": room.current_temp,
        "next_change": (
            {"at": change[0].isoformat(), "temp": change[1]} if change else None
        ),
        "sync_state": room.sync_state,
        "last_error": room.last_error,
        "available": room.trv.available,
        "today_profile": today_name,
        "today_points": [{"minute": p.minute, "temp": p.temp} for p in today_profile.points],
        "day_start_temp": room.plan.profiles[yesterday_name].points[-1].temp,
        "override_hours": room.room.override_hours,
        "min_temp": home.options.min_temp,
    }


@websocket_api.websocket_command({vol.Required("type"): "heating_plan/rooms"})
@callback
def ws_rooms(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the rooms."""
    rooms = [
        room_to_dict(hass, room) for home in homes(hass) for room in home.rooms.values()
    ]
    connection.send_result(msg["id"], rooms)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "heating_plan/set_plan",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("profiles"): {cv.string: str},
        vol.Required("week"): [cv.string],
    }
)
@websocket_api.async_response
async def ws_set_plan(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save the plan of a room."""
    try:
        room = room_for_entity(hass, msg["entity_id"])
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "unknown_room", str(err))
        return
    try:
        plan = parse_plan(msg["profiles"], msg["week"])
        await room.set_plan(plan)
    except PlanError as err:
        connection.send_error(msg["id"], "invalid_plan", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})
