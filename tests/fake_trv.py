"""A fake Bosch BTH-RA thermostat, made of Zigbee2MQTT-like entities."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# (domain, property) as Zigbee2MQTT 2.14.3 makes them for a BTH-RA.
ENTITIES = [
    ("climate", None),
    ("select", "operating_mode"),
    ("sensor", "setpoint_change_source"),
    ("number", "remote_temperature"),
    ("sensor", "local_temperature"),
    *[("text", f"weekly_schedule_{day}") for day in DAYS],
]


@dataclass
class FakeTrv:
    """The entity ids of a fake thermostat and the service calls it got."""

    hass: HomeAssistant
    name: str
    climate: str
    mode_entity: str
    source_entity: str
    remote_entity: str
    day_entities: list[str]
    calls: list[tuple[str, str, dict]] = field(default_factory=list)
    lose_days: set[int] = field(default_factory=set)
    """Weekdays for which a write does not change the state (lost write)."""
    late_days: set[int] = field(default_factory=set)
    """Weekdays for which the state changes a moment after the write."""

    def set_available(self, available: bool) -> None:
        state = "heat" if available else "unavailable"
        old = self.hass.states.get(self.climate)
        attrs = dict(old.attributes) if old else {ATTR_TEMPERATURE: 20.0}
        self.hass.states.async_set(self.climate, state, attrs)

    def set_mode(self, mode: str) -> None:
        self.hass.states.async_set(self.mode_entity, mode)

    def set_source(self, source: str) -> None:
        self.hass.states.async_set(self.source_entity, source)

    def set_setpoint(self, temp: float) -> None:
        state = self.hass.states.get(self.climate)
        self.hass.states.async_set(
            self.climate, state.state, {**state.attributes, ATTR_TEMPERATURE: temp}
        )

    def set_day(self, weekday: int, text: str) -> None:
        self.hass.states.async_set(self.day_entities[weekday], text)

    def day_writes(self) -> list[tuple[int, str]]:
        return [
            (self.day_entities.index(data[ATTR_ENTITY_ID]), data["value"])
            for domain, service, data in self.calls
            if (domain, service) == ("text", "set_value")
        ]

    def services(self, domain: str, service: str) -> list[dict]:
        return [d for dm, sv, d in self.calls if (dm, sv) == (domain, service)]

    def clear_calls(self) -> None:
        self.calls.clear()


def _register_services(hass: HomeAssistant, trvs: list[FakeTrv]) -> None:
    """Register fake services that act like Zigbee2MQTT and the thermostat."""

    def find(entity_id: str) -> FakeTrv | None:
        for trv in trvs:
            if entity_id in (
                trv.climate,
                trv.mode_entity,
                trv.remote_entity,
                *trv.day_entities,
            ):
                return trv
        return None

    async def handle(call: ServiceCall) -> None:
        ids = call.data[ATTR_ENTITY_ID]
        entity_id = ids[0] if isinstance(ids, list) else ids
        trv = find(entity_id)
        if trv is None:
            return
        data = {**call.data, ATTR_ENTITY_ID: entity_id}
        trv.calls.append((call.domain, call.service, data))
        if call.domain == "text":
            weekday = trv.day_entities.index(entity_id)
            if weekday in trv.late_days:
                hass.loop.call_soon(trv.set_day, weekday, call.data["value"])
            elif weekday not in trv.lose_days:
                trv.set_day(weekday, call.data["value"])
        elif call.domain == "select":
            trv.set_mode(call.data["option"])
        elif call.domain == "climate":
            trv.set_setpoint(call.data[ATTR_TEMPERATURE])
            trv.set_source("externally")

    for domain, service in (
        ("text", "set_value"),
        ("select", "select_option"),
        ("climate", "set_temperature"),
        ("number", "set_value"),
    ):
        hass.services.async_register(domain, service, handle)


def add_fake_trvs(hass: HomeAssistant, names: list[str], *, skip: str | None = None) -> list[FakeTrv]:
    """Add fake thermostats to the registries and the state machine."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    trvs: list[FakeTrv] = []
    for index, name in enumerate(names):
        ieee = f"0x00000000000000{index:02x}"
        device = dev_reg.async_get_or_create(
            config_entry_id=mqtt_entry.entry_id,
            identifiers={("mqtt", f"zigbee2mqtt_{ieee}")},
            manufacturer="Bosch",
            model="Radiator thermostat II",
            name=name,
        )
        ids: dict[str, str] = {}
        for domain, prop in ENTITIES:
            if skip is not None and prop == skip:
                continue
            unique = f"{ieee}_{prop or domain}_zigbee2mqtt"
            entry = ent_reg.async_get_or_create(
                domain,
                "mqtt",
                unique,
                suggested_object_id=f"{name}_{prop}" if prop else name,
                device_id=device.id,
                config_entry=mqtt_entry,
            )
            ids[prop or domain] = entry.entity_id
        trv = FakeTrv(
            hass=hass,
            name=name,
            climate=ids["climate"],
            mode_entity=ids.get("operating_mode", ""),
            source_entity=ids.get("setpoint_change_source", ""),
            remote_entity=ids.get("remote_temperature", ""),
            day_entities=[ids.get(f"weekly_schedule_{d}", "") for d in DAYS],
        )
        hass.states.async_set(trv.climate, "heat", {ATTR_TEMPERATURE: 20.0})
        if trv.mode_entity:
            trv.set_mode("manual")
        if trv.source_entity:
            trv.set_source("manual")
        for weekday, entity_id in enumerate(trv.day_entities):
            if entity_id:
                hass.states.async_set(entity_id, "")
        trvs.append(trv)
    _register_services(hass, trvs)
    return trvs
