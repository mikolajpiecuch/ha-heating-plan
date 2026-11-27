"""Persistent state: room plans and active overrides."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .model import DayProfile, Override, RoomPlan

STORAGE_VERSION = 1


class StoreData(TypedDict):
    """The stored data. The keys are room subentry ids."""

    plans: dict[str, RoomPlan]
    overrides: dict[str, Override]


def _plan_to_dict(plan: RoomPlan) -> dict[str, Any]:
    return {
        "profiles": {name: p.to_z2m() for name, p in plan.profiles.items()},
        "week": list(plan.week),
    }


def _plan_from_dict(data: dict[str, Any]) -> RoomPlan:
    return RoomPlan(
        profiles={name: DayProfile.parse(text) for name, text in data["profiles"].items()},
        week=tuple(data["week"]),
    )


def _override_to_dict(override: Override) -> dict[str, Any]:
    return {
        "temp": override.temp,
        "until": override.until.astimezone(UTC).isoformat() if override.until else None,
        "kind": override.kind,
        "source": override.source,
    }


def _override_from_dict(data: dict[str, Any]) -> Override:
    until = data.get("until")
    return Override(
        temp=data.get("temp"),
        until=datetime.fromisoformat(until).astimezone(UTC) if until else None,
        kind=data["kind"],
        source=data["source"],
    )


class PlanStore:
    """Load and save the state of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )

    async def load(self) -> StoreData:
        """Load the data. Missing data gives empty plans and overrides."""
        raw = await self._store.async_load() or {}
        return StoreData(
            plans={k: _plan_from_dict(v) for k, v in raw.get("plans", {}).items()},
            overrides={
                k: _override_from_dict(v) for k, v in raw.get("overrides", {}).items()
            },
        )

    async def save(self, data: StoreData) -> None:
        """Save the data now."""
        await self._store.async_save(
            {
                "plans": {k: _plan_to_dict(v) for k, v in data["plans"].items()},
                "overrides": {
                    k: _override_to_dict(v) for k, v in data["overrides"].items()
                },
            }
        )

    async def remove(self) -> None:
        """Delete the stored data."""
        await self._store.async_remove()
