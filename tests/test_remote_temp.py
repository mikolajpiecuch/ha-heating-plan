"""Tests for sending the room temperature to the thermostat."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.heating_plan import controller as controller_module

from .helpers import Setup, settle, setup_home

TZ = ZoneInfo("Europe/Berlin")
SENSOR = "sensor.office_air"


@pytest.fixture(autouse=True)
def short_readback():
    with patch.object(controller_module, "READBACK_TIMEOUT_PER_DAY", 0):
        yield


@pytest.fixture(autouse=True)
def wednesday(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(datetime(2026, 12, 2, 9, 0, tzinfo=TZ))


def pushed(s: Setup) -> list[float]:
    return [c["value"] for c in s.trvs[0].services("number", "set_value")]


def set_temp(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(SENSOR, value, {"device_class": "temperature"})


async def setup_with_sensor(hass: HomeAssistant, value: str = "21.0") -> Setup:
    set_temp(hass, value)
    return await setup_home(hass, sensors={"Biuro": SENSOR})


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await settle(hass)


async def test_pushes_at_start(hass: HomeAssistant) -> None:
    s = await setup_with_sensor(hass)
    assert pushed(s) == [21.0]


async def test_pushes_every_15_min(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    s = await setup_with_sensor(hass)
    for _ in range(3):
        await tick(hass, freezer, timedelta(minutes=5))
        set_temp(hass, "21.0")  # the same value again: a fresh report
        await settle(hass)
    assert pushed(s) == [21.0, 21.0]


async def test_pushes_on_change_0_2(hass: HomeAssistant) -> None:
    s = await setup_with_sensor(hass)
    set_temp(hass, "21.1")
    await settle(hass)
    assert pushed(s) == [21.0]
    set_temp(hass, "21.2")
    await settle(hass)
    assert pushed(s) == [21.0, 21.2]
    set_temp(hass, "21.05")
    await settle(hass)
    assert pushed(s) == [21.0, 21.2]
    set_temp(hass, "20.9")
    await settle(hass)
    assert pushed(s) == [21.0, 21.2, 20.8]


async def test_stale_sensor_not_pushed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_with_sensor(hass)
    await tick(hass, freezer, timedelta(minutes=61))
    assert pushed(s) == [21.0]
    # A fresh report is sent again.
    set_temp(hass, "21.0")
    await settle(hass)
    assert pushed(s) == [21.0, 21.0]


async def test_stale_sensor_at_start_not_pushed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_temp(hass, "21.0")
    freezer.tick(timedelta(hours=2))
    s = await setup_home(hass, sensors={"Biuro": SENSOR})
    assert pushed(s) == []


async def test_unavailable_sensor_not_pushed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_with_sensor(hass, "unavailable")
    set_temp(hass, "unknown")
    await tick(hass, freezer, timedelta(minutes=15))
    set_temp(hass, "abc")
    await settle(hass)
    assert pushed(s) == []


async def test_offline_thermostat_not_pushed(hass: HomeAssistant) -> None:
    s = await setup_with_sensor(hass)
    s.trvs[0].set_available(False)
    s.trvs[0].clear_calls()
    set_temp(hass, "23.0")
    await settle(hass)
    assert pushed(s) == []


async def test_pushes_at_once_on_reconnect(hass: HomeAssistant) -> None:
    s = await setup_with_sensor(hass)
    s.trvs[0].set_available(False)
    await settle(hass)
    set_temp(hass, "21.0")  # fresh, but the thermostat is offline
    await settle(hass)
    s.trvs[0].clear_calls()
    s.trvs[0].set_available(True)
    await settle(hass)
    assert pushed(s) == [21.0]


async def test_no_sensor_configured_noop(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    s = await setup_home(hass)
    await tick(hass, freezer, timedelta(minutes=30))
    assert pushed(s) == []
    assert s.room().current_temp is None
