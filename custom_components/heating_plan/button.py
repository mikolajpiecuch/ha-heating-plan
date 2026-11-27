"""Buttons: cancel the override of a room, cancel all overrides."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingPlanConfigEntry
from .const import DOMAIN
from .controller import HomeController, RoomController
from .entity import RoomEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingPlanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one button for each room and one for the home."""
    home = entry.runtime_data
    async_add_entities([CancelAllButton(home)])
    for room in home.rooms.values():
        async_add_entities([CancelOverrideButton(room)], config_subentry_id=room.room_id)


class CancelOverrideButton(RoomEntity, ButtonEntity):
    """End the override of a room."""

    _attr_translation_key = "cancel_override"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "cancel_override")

    async def async_press(self) -> None:
        """Cancel the override."""
        await self.room.cancel_override()


class CancelAllButton(ButtonEntity):
    """End the overrides of all rooms."""

    _attr_has_entity_name = True
    _attr_translation_key = "cancel_all"

    def __init__(self, home: HomeController) -> None:
        self.home = home
        self._attr_unique_id = f"{home.entry.entry_id}_cancel_all"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, home.entry.entry_id)},
            name=home.entry.title,
            manufacturer="Heating Plan",
            model="Home",
        )

    async def async_press(self) -> None:
        """Cancel all overrides."""
        await self.home.cancel_all()
