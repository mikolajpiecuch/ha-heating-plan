"""Fake thermostat climate entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant

from .common import DOMAIN, FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities(
        FakeClimate(i, name) for i, name in enumerate(hass.data[DOMAIN]["names"])
    )


class FakeClimate(FakeEntity, ClimateEntity):
    """Setpoint and room temperature."""

    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5
    _attr_max_temp = 30

    def __init__(self, index: int, name: str) -> None:
        super().__init__(index, name, None)
        self._attr_target_temperature = 20
        self._attr_current_temperature = 19.5 + index

    async def async_added_to_hass(self) -> None:
        self.device_state()["climate"] = self

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
        self.async_write_ha_state()
        source = self.device_state().get("source")
        if source is not None:
            source.set_value("externally")
