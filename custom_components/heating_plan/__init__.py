"""The Heating Plan integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_ROOM, default_plan
from .store import PlanStore


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Plan from a config entry."""
    store = PlanStore(hass, entry.entry_id)
    data = await store.load()
    rooms = {
        subentry_id
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_ROOM
    }
    changed = False
    for room_id in rooms - data["plans"].keys():
        data["plans"][room_id] = default_plan()
        changed = True
    for room_id in set(data["plans"]) - rooms:
        del data["plans"][room_id]
        data["overrides"].pop(room_id, None)
        changed = True
    if changed:
        await store.save(data)

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when rooms change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored data when the entry is deleted."""
    await PlanStore(hass, entry.entry_id).remove()
