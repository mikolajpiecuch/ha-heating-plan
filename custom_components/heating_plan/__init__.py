"""The Heating Plan integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_ROOM, Options, RoomConfig, default_plan
from .controller import HomeController, RoomController
from .store import PlanStore
from .trv import BoschTrv, TrvError

_LOGGER = logging.getLogger(__name__)

type HeatingPlanConfigEntry = ConfigEntry[HomeController]


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

    entry.runtime_data = home
    await home.start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: HeatingPlanConfigEntry) -> None:
    """Reload the entry when rooms or options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HeatingPlanConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored data when the entry is deleted."""
    await PlanStore(hass, entry.entry_id).remove()
