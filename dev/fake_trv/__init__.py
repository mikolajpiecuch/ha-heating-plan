"""Fake Bosch BTH-RA thermostats for development (not part of the integration).

The entities have the same unique ids as Zigbee2MQTT makes
(``<ieee>_<property>_zigbee2mqtt``), so that Heating Plan finds them.
Configuration (configuration.yaml):

    fake_trv:
      - office
      - bedroom
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .common import DOMAIN

PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.TEXT]

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.All(cv.ensure_list, [cv.slug])}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Make a config entry with the names from configuration.yaml."""
    names = config.get(DOMAIN, [])
    hass.data[DOMAIN] = {"names": names, "devices": {}}
    if not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Add the fake entities."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the fake entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
