"""The Heating Plan integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, SUBENTRY_ROOM, Options, RoomConfig, default_plan
from .controller import HomeController, RoomController
from .store import PlanStore
from .trv import BoschTrv, TrvError

_LOGGER = logging.getLogger(__name__)

type HeatingPlanConfigEntry = ConfigEntry[HomeController]

PLATFORMS = [Platform.BUTTON, Platform.CLIMATE, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_URL = f"/{DOMAIN}/heating-plan-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "heating-plan-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and the websocket commands."""
    from .services import async_register_services  # noqa: PLC0415
    from .websocket import async_register_websocket  # noqa: PLC0415

    async_register_services(hass)
    async_register_websocket(hass)
    await async_register_card(hass)
    return True


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and load it in the frontend."""
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")


async def async_setup_entry(hass: HomeAssistant, entry: HeatingPlanConfigEntry) -> bool:
    """Set up Heating Plan from a config entry."""
    store = PlanStore(hass, entry.entry_id)
    data = await store.load()
    rooms = {
        subentry_id: RoomConfig.from_subentry(subentry)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_ROOM
    }
    changed = False
    for room_id in rooms.keys() - data["plans"].keys():
        data["plans"][room_id] = default_plan()
        changed = True
    for room_id in set(data["plans"]) - rooms.keys():
        del data["plans"][room_id]
        changed = True
    for room_id in set(data["overrides"]) - rooms.keys():
        del data["overrides"][room_id]
        changed = True
    if changed:
        await store.save(data)

    home = HomeController(hass, entry, store, data, Options.from_entry(entry))
    for room_id, room in rooms.items():
        try:
            trv = BoschTrv.from_climate(hass, room.climate_entity_id)
        except TrvError as err:
            # The other rooms still work.
            _LOGGER.error("Room %s is not started: %s", room.name, err)
            continue
        home.rooms[room_id] = RoomController(hass, room_id, room, trv, store, home)

    _async_setup_devices(hass, entry, home)
    entry.runtime_data = home
    await home.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _async_setup_devices(
    hass: HomeAssistant, entry: HeatingPlanConfigEntry, home: HomeController
) -> None:
    """Make one device for each room, in the area of the room."""
    registry = dr.async_get(hass)
    for room in home.rooms.values():
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=room.room_id,
            identifiers={(DOMAIN, room.room_id)},
            name=room.name,
            manufacturer="Heating Plan",
            model="Room",
        )
        if device.area_id is None:
            registry.async_update_device(device.id, area_id=room.room.area_id)


async def _async_reload(hass: HomeAssistant, entry: HeatingPlanConfigEntry) -> None:
    """Reload the entry when rooms or options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HeatingPlanConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored data when the entry is deleted."""
    await PlanStore(hass, entry.entry_id).remove()
