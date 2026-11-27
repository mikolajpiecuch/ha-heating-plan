"""Tests for the Bosch BTH-RA adapter."""

import pytest

from homeassistant.core import HomeAssistant

from custom_components.heating_plan.model import DayProfile
from custom_components.heating_plan.trv import BoschTrv, TrvError

from .fake_trv import add_fake_trvs


async def test_discovers_entities(hass: HomeAssistant) -> None:
    [fake, other] = add_fake_trvs(hass, ["office", "bedroom"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    assert trv.climate_entity_id == fake.climate
    assert trv.mode_entity_id == fake.mode_entity
    assert trv.source_entity_id == fake.source_entity
    assert trv.remote_entity_id == fake.remote_entity
    assert trv.day_entity_ids == fake.day_entities
    assert other.climate not in trv.day_entity_ids


async def test_state_properties(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    fake.set_mode("schedule")
    fake.set_source("externally")
    fake.set_setpoint(21.5)
    fake.set_day(2, "06:00/21")
    assert trv.available
    assert trv.mode == "schedule"
    assert trv.change_source == "externally"
    assert trv.setpoint == 21.5
    assert trv.day_text(2) == "06:00/21"
    assert trv.day_text(3) == ""


@pytest.mark.parametrize(
    "skip", ["operating_mode", "setpoint_change_source", "remote_temperature", "weekly_schedule_friday"]
)
async def test_missing_entity_raises(hass: HomeAssistant, skip: str) -> None:
    [fake] = add_fake_trvs(hass, ["office"], skip=skip)
    with pytest.raises(TrvError):
        BoschTrv.from_climate(hass, fake.climate)


async def test_longer_property_is_not_matched(hass: HomeAssistant) -> None:
    """A property that starts with the same text is not the entity."""
    from homeassistant.helpers import entity_registry as er

    [fake] = add_fake_trvs(hass, ["office"])
    ent_reg = er.async_get(hass)
    climate = ent_reg.async_get(fake.climate)
    # Added before the real one; it must not win.
    ent_reg.async_remove(fake.mode_entity)
    other = ent_reg.async_get_or_create(
        "select",
        "mqtt",
        "0x0000000000000000_operating_mode_extra_zigbee2mqtt",
        device_id=climate.device_id,
    )
    real = ent_reg.async_get_or_create(
        "select",
        "mqtt",
        "0x0000000000000000_operating_mode_zigbee2mqtt",
        device_id=climate.device_id,
    )
    trv = BoschTrv.from_climate(hass, fake.climate)
    assert trv.mode_entity_id == real.entity_id
    assert trv.mode_entity_id != other.entity_id


async def test_unknown_climate_raises(hass: HomeAssistant) -> None:
    with pytest.raises(TrvError):
        BoschTrv.from_climate(hass, "climate.nothing")


async def test_write_day_calls_text_set_value(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    await trv.write_day(4, DayProfile.parse("22:00/17 6:00/21"))
    assert fake.day_writes() == [(4, "06:00/21 22:00/17")]


async def test_set_mode_and_setpoint(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    await trv.set_mode("schedule")
    await trv.set_setpoint(22.5)
    assert fake.services("select", "select_option")[0]["option"] == "schedule"
    assert fake.services("climate", "set_temperature")[0]["temperature"] == 22.5
    assert trv.mode == "schedule"


async def test_push_remote_temp_rounds_to_0_2(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    await trv.push_remote_temp(21.33)
    await trv.push_remote_temp(21.29)
    values = [d["value"] for d in fake.services("number", "set_value")]
    assert values == [21.4, 21.2]


async def test_unavailable_when_climate_unavailable(hass: HomeAssistant) -> None:
    [fake] = add_fake_trvs(hass, ["office"])
    trv = BoschTrv.from_climate(hass, fake.climate)
    fake.set_available(False)
    assert not trv.available
    hass.states.async_remove(fake.climate)
    assert not trv.available
    assert trv.setpoint is None
