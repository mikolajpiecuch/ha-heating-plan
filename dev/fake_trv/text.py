"""Fake weekly schedule days. A write is reported back after 2 seconds."""

from __future__ import annotations

import asyncio

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant

from .common import DAYS, DOMAIN, FakeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities(
        FakeDay(i, name, day)
        for i, name in enumerate(hass.data[DOMAIN]["names"])
        for day in DAYS
    )


class FakeDay(FakeEntity, TextEntity):
    _attr_native_max = 255

    def __init__(self, index: int, name: str, day: str) -> None:
        super().__init__(index, name, f"weekly_schedule_{day}")
        self._attr_native_value = ""

    async def async_set_value(self, value: str) -> None:
        async def report() -> None:
            await asyncio.sleep(2)
            self._attr_native_value = value
            self.async_write_ha_state()

        self.hass.async_create_background_task(report(), "fake_trv report")
