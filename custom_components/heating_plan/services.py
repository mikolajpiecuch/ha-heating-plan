"""Services and helpers to find a room from an entity id."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .controller import HomeController, RoomController
from .model import DayProfile, PlanError, RoomPlan

ATTR_MODE = "mode"
ATTR_HOURS = "hours"
ATTR_PROFILES = "profiles"
ATTR_WEEK = "week"

OVERRIDE_MODES = ["timed", "until_next", "until_cancel"]

SET_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_MODE, default="timed"): vol.In(OVERRIDE_MODES),
        vol.Optional(ATTR_HOURS): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=168)),
    }
)
CANCEL_OVERRIDE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})
SET_PLAN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_PROFILES): {cv.string: cv.string},
        vol.Required(ATTR_WEEK): [cv.string],
    }
)


def homes(hass: HomeAssistant) -> list[HomeController]:
    """The loaded homes (one at most)."""
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def climate_entity_id(hass: HomeAssistant, room: RoomController) -> str | None:
    """The climate entity of a room."""
    return er.async_get(hass).async_get_entity_id(
        "climate", DOMAIN, f"{room.room_id}_climate"
    )


def room_for_entity(hass: HomeAssistant, entity_id: str) -> RoomController:
    """The room of a climate entity of this integration."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform == DOMAIN:
        for home in homes(hass):
            for room in home.rooms.values():
                if entry.unique_id == f"{room.room_id}_climate":
                    return room
    raise ServiceValidationError(
        f"{entity_id} is not a room of Heating Plan",
        translation_domain=DOMAIN,
        translation_key="unknown_room",
        translation_placeholders={"entity_id": entity_id},
    )


def parse_plan(profiles: dict[str, str], week: list[str]) -> RoomPlan:
    """Make a plan from texts. Raise PlanError with a clear message."""
    parsed: dict[str, DayProfile] = {}
    for name, text in profiles.items():
        try:
            parsed[name] = DayProfile.parse(text)
        except PlanError as err:
            raise PlanError(f"Profile '{name}': {err}") from err
    plan = RoomPlan(profiles=parsed, week=tuple(week))
    plan.validate()
    return plan


def _plan_error(err: PlanError) -> ServiceValidationError:
    return ServiceValidationError(
        str(err),
        translation_domain=DOMAIN,
        translation_key="invalid_plan",
        translation_placeholders={"error": str(err)},
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""

    async def set_override(call: ServiceCall) -> None:
        for entity_id in call.data[ATTR_ENTITY_ID]:
            room = room_for_entity(hass, entity_id)
            temp = call.data.get(ATTR_TEMPERATURE)
            if temp is None:
                temp = room.target
            await room.set_override(temp, call.data[ATTR_MODE], call.data.get(ATTR_HOURS))

    async def cancel_override(call: ServiceCall) -> None:
        entity_ids: list[str] | None = call.data.get(ATTR_ENTITY_ID)
        if not entity_ids:
            for home in homes(hass):
                await home.cancel_all()
            return
        for entity_id in entity_ids:
            await room_for_entity(hass, entity_id).cancel_override()

    async def set_plan(call: ServiceCall) -> None:
        room = room_for_entity(hass, call.data[ATTR_ENTITY_ID])
        try:
            plan = parse_plan(call.data[ATTR_PROFILES], call.data[ATTR_WEEK])
            await room.set_plan(plan)
        except PlanError as err:
            raise _plan_error(err) from err

    hass.services.async_register(DOMAIN, "set_override", set_override, SET_OVERRIDE_SCHEMA)
    hass.services.async_register(
        DOMAIN, "cancel_override", cancel_override, CANCEL_OVERRIDE_SCHEMA
    )
    hass.services.async_register(DOMAIN, "set_plan", set_plan, SET_PLAN_SCHEMA)


def plan_to_dict(plan: RoomPlan) -> dict[str, Any]:
    """The plan as texts."""
    return {
        "profiles": {name: p.to_z2m() for name, p in plan.profiles.items()},
        "week": list(plan.week),
    }
