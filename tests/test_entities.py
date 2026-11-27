"""Tests for the entities, the services and the websocket commands."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.heating_plan import controller as controller_module
from custom_components.heating_plan.const import DOMAIN
from custom_components.heating_plan.model import DayProfile, Override

from .helpers import Setup, settle, setup_home

TZ = ZoneInfo("Europe/Berlin")
SENSOR = "sensor.office_air"


@pytest.fixture(autouse=True)
def short_readback():
    with patch.object(controller_module, "READBACK_TIMEOUT_PER_DAY", 0):
        yield


@pytest.fixture(autouse=True)
def at_seven(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(datetime(2026, 12, 2, 7, 0, tzinfo=TZ))


def entity_id(hass: HomeAssistant, s: Setup, key: str, index: int = 0) -> str:
    room_id = s.room(index).room_id
    platform = key.split(".")[0]
    unique_id = f"{room_id}_{key.split('.')[1]}"
    eid = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert eid is not None, unique_id
    return eid


async def setup(hass: HomeAssistant) -> Setup:
    hass.states.async_set(SENSOR, "20.5", {"device_class": "temperature"})
    return await setup_home(hass, ("Biuro", "Sypialnia"), sensors={"Biuro": SENSOR})


async def test_climate_entity(hass: HomeAssistant) -> None:
    s = await setup(hass)
    climate = entity_id(hass, s, "climate.climate")
    state = hass.states.get(climate)
    assert state.state == HVACMode.AUTO
    assert state.attributes["hvac_modes"] == [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    assert state.attributes["temperature"] == 21  # Praca 06:00/21
    assert state.attributes["current_temperature"] == 20.5
    assert dt_util.parse_datetime(state.attributes["next_change_at"]) == datetime(
        2026, 12, 2, 8, 0, tzinfo=TZ
    )
    assert state.attributes["next_change_temp"] == 18
    assert state.attributes["sync_state"] == "synced"
    assert state.attributes["override_until"] is None
    assert state.attributes["override_kind"] is None


async def test_entities_have_room_area(hass: HomeAssistant) -> None:
    s = await setup(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    for key in (
        "climate.climate",
        "sensor.next_change",
        "sensor.override_until",
        "sensor.sync_state",
        "button.cancel_override",
    ):
        entry = ent_reg.async_get(entity_id(hass, s, key))
        device = dev_reg.async_get(entry.device_id)
        assert device.area_id == s.room().room.area_id
        assert entry.config_subentry_id == s.room().room_id


async def test_set_temperature_is_timed_override(hass: HomeAssistant) -> None:
    s = await setup(hass)
    climate = entity_id(hass, s, "climate.climate")
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": climate, "temperature": 23}, blocking=True
    )
    await settle(hass)
    assert s.room().override == Override(
        temp=23, until=dt_util.utcnow() + timedelta(hours=3), kind="timed", source="ha"
    )
    state = hass.states.get(climate)
    assert state.attributes["temperature"] == 23
    assert state.attributes["override_kind"] == "timed"
    assert state.attributes["override_until"] == s.room().override.until.isoformat()


async def test_hvac_modes(hass: HomeAssistant) -> None:
    s = await setup(hass)
    climate = entity_id(hass, s, "climate.climate")

    async def set_mode(mode: HVACMode) -> None:
        await hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": mode}, blocking=True
        )
        await settle(hass)

    await set_mode(HVACMode.HEAT)
    assert s.room().override == Override(21, None, "until_cancel", "ha")
    assert hass.states.get(climate).state == HVACMode.HEAT

    await set_mode(HVACMode.OFF)
    assert s.room().override == Override(None, None, "until_cancel", "ha")
    assert hass.states.get(climate).state == HVACMode.OFF
    assert hass.states.get(climate).attributes["temperature"] is None

    # Heat after off uses the plan target, not the pause.
    await set_mode(HVACMode.HEAT)
    assert s.room().override.temp == 21

    await set_mode(HVACMode.AUTO)
    assert s.room().override is None
    assert hass.states.get(climate).state == HVACMode.AUTO


async def test_sensors(hass: HomeAssistant) -> None:
    s = await setup(hass)
    next_change = hass.states.get(entity_id(hass, s, "sensor.next_change"))
    assert dt_util.parse_datetime(next_change.state) == datetime(2026, 12, 2, 8, 0, tzinfo=TZ)
    assert next_change.attributes["temperature"] == 18
    until = hass.states.get(entity_id(hass, s, "sensor.override_until"))
    assert until.state == "unknown"
    sync = hass.states.get(entity_id(hass, s, "sensor.sync_state"))
    assert sync.state == "synced"
    assert sync.attributes["options"] == ["synced", "pending", "error"]

    await s.room().set_override(23)
    await settle(hass)
    until = hass.states.get(entity_id(hass, s, "sensor.override_until"))
    assert dt_util.parse_datetime(until.state) == s.room().override.until


async def test_sync_state_follows_thermostat(hass: HomeAssistant) -> None:
    s = await setup(hass)
    s.trvs[0].set_available(False)
    await settle(hass)
    assert hass.states.get(entity_id(hass, s, "sensor.sync_state")).state == "pending"


async def test_buttons(hass: HomeAssistant) -> None:
    s = await setup(hass)
    await s.room(0).set_override(23, kind="until_cancel")
    await s.room(1).set_override(22, kind="until_cancel")
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id(hass, s, "button.cancel_override")}, blocking=True
    )
    await settle(hass)
    assert s.room(0).override is None
    assert s.room(1).override is not None

    all_button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{s.entry.entry_id}_cancel_all"
    )
    await hass.services.async_call("button", "press", {"entity_id": all_button}, blocking=True)
    await settle(hass)
    assert s.room(1).override is None


async def test_service_set_override(hass: HomeAssistant) -> None:
    s = await setup(hass)
    climate = entity_id(hass, s, "climate.climate")
    await hass.services.async_call(
        DOMAIN,
        "set_override",
        {"entity_id": climate, "temperature": 22, "mode": "timed", "hours": 1},
        blocking=True,
    )
    assert s.room().override.until == dt_util.utcnow() + timedelta(hours=1)
    await hass.services.async_call(
        DOMAIN, "set_override", {"entity_id": climate, "mode": "until_next"}, blocking=True
    )
    # Without a temperature the current target is kept.
    assert s.room().override.temp == 22
    assert s.room().override.until == datetime(2026, 12, 2, 8, 0, tzinfo=TZ)


async def test_service_cancel_override(hass: HomeAssistant) -> None:
    s = await setup(hass)
    await s.room(0).set_override(23, kind="until_cancel")
    await s.room(1).set_override(22, kind="until_cancel")
    await hass.services.async_call(
        DOMAIN,
        "cancel_override",
        {"entity_id": entity_id(hass, s, "climate.climate", 1)},
        blocking=True,
    )
    assert s.room(0).override is not None
    assert s.room(1).override is None
    await hass.services.async_call(DOMAIN, "cancel_override", {}, blocking=True)
    assert s.room(0).override is None


async def test_service_set_plan(hass: HomeAssistant) -> None:
    s = await setup(hass)
    climate = entity_id(hass, s, "climate.climate")
    await hass.services.async_call(
        DOMAIN,
        "set_plan",
        {
            "entity_id": climate,
            "profiles": {"Dzień": "06:00/20 22:00/16"},
            "week": ["Dzień"] * 7,
        },
        blocking=True,
    )
    assert s.room().plan.profiles == {"Dzień": DayProfile.parse("06:00/20 22:00/16")}

    eleven = " ".join(f"{h:02d}:00/20" for h in range(11))
    with pytest.raises(ServiceValidationError, match="at most 10 points"):
        await hass.services.async_call(
            DOMAIN,
            "set_plan",
            {"entity_id": climate, "profiles": {"Dzień": eleven}, "week": ["Dzień"] * 7},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError, match="Brak"):
        await hass.services.async_call(
            DOMAIN,
            "set_plan",
            {"entity_id": climate, "profiles": {"Dzień": "06:00/20"}, "week": ["Brak"] * 7},
            blocking=True,
        )


async def test_service_unknown_entity(hass: HomeAssistant) -> None:
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_override", {"entity_id": "climate.nothing"}, blocking=True
        )


async def test_websocket_rooms(hass: HomeAssistant, hass_ws_client) -> None:
    s = await setup(hass)
    await s.room().set_override(23)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "heating_plan/rooms"})
    msg = await client.receive_json()
    assert msg["success"]
    rooms = msg["result"]
    assert [r["name"] for r in rooms] == ["Biuro", "Sypialnia"]
    room = rooms[0]
    assert room["entity_id"] == entity_id(hass, s, "climate.climate")
    assert room["area_id"] == s.room().room.area_id
    assert room["plan"]["profiles"]["Praca"] == "06:00/21 08:00/18 16:00/21 22:00/17"
    assert room["plan"]["week"] == ["Praca"] * 5 + ["Wolne"] * 2
    assert room["override"]["temp"] == 23
    assert room["override"]["kind"] == "timed"
    assert room["target"] == 23
    assert room["current_temp"] == 20.5
    assert room["sync_state"] == "synced"
    assert room["next_change"]["temp"] == 18  # plan at 10:00, the end of the override
    assert room["today_profile"] == "Praca"
    assert room["today_points"] == [
        {"minute": 360, "temp": 21},
        {"minute": 480, "temp": 18},
        {"minute": 960, "temp": 21},
        {"minute": 1320, "temp": 17},
    ]
    assert room["day_start_temp"] == 17
    assert room["min_temp"] == 7


async def test_websocket_set_plan(hass: HomeAssistant, hass_ws_client) -> None:
    s = await setup(hass)
    client = await hass_ws_client(hass)
    climate = entity_id(hass, s, "climate.climate")
    await client.send_json(
        {
            "id": 1,
            "type": "heating_plan/set_plan",
            "entity_id": climate,
            "profiles": {"Praca": "06:00/22", "Wolne": "08:00/20"},
            "week": ["Praca"] * 5 + ["Wolne"] * 2,
        }
    )
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"] == {"ok": True}
    assert s.room().plan.profiles["Praca"] == DayProfile.parse("06:00/22")

    await client.send_json(
        {
            "id": 2,
            "type": "heating_plan/set_plan",
            "entity_id": climate,
            "profiles": {"Praca": ""},
            "week": ["Praca"] * 7,
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_plan"
    assert "at least one point" in msg["error"]["message"]
