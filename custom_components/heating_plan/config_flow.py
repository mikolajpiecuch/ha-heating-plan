"""Config flow for the Heating Plan integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .calendar_rules import normalize
from .const import (
    CONF_AREA_ID,
    CONF_CALENDAR_ENTITY_ID,
    CONF_CLIMATE_ENTITY_ID,
    CONF_HOLIDAY_PROFILE,
    CONF_HOLIDAYS_ENABLED,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_OVERRIDE_HOURS,
    CONF_SENSOR_ENTITY_ID,
    DEFAULT_OVERRIDE_HOURS,
    DEFAULT_PROFILES,
    DOMAIN,
    SUBENTRY_ROOM,
    Options,
)
from .trv import BoschTrv, TrvError

DEFAULT_NAME = "Dom"
RESERVED_NAMES = {normalize(n) for n in (*DEFAULT_PROFILES, "wyłącz")}


class HeatingPlanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Heating Plan."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the name of the home."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=DEFAULT_NAME): str}
            ),
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Rooms are subentries."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HeatingPlanOptionsFlow:
        """Options of the whole home."""
        return HeatingPlanOptionsFlow()


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add a room."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the room data."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            climate = user_input[CONF_CLIMATE_ENTITY_ID]
            for subentry in entry.subentries.values():
                if subentry.unique_id == climate:
                    return self.async_abort(reason="already_configured")
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_empty"
            elif normalize(name) in RESERVED_NAMES:
                # The name must not make calendar titles unclear.
                errors[CONF_NAME] = "name_reserved"
            elif any(
                normalize(s.title) == normalize(name)
                for s in entry.subentries.values()
            ):
                errors[CONF_NAME] = "name_exists"
            try:
                BoschTrv.from_climate(self.hass, climate)
            except TrvError:
                errors[CONF_CLIMATE_ENTITY_ID] = "trv_not_supported"
            if not errors:
                data = {
                    **user_input,
                    CONF_NAME: name,
                    CONF_OVERRIDE_HOURS: float(
                        user_input.get(CONF_OVERRIDE_HOURS, DEFAULT_OVERRIDE_HOURS)
                    ),
                }
                return self.async_create_entry(title=name, data=data, unique_id=climate)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_AREA_ID): selector.AreaSelector(),
                vol.Required(CONF_CLIMATE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate", integration="mqtt")
                ),
                vol.Optional(CONF_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor", device_class="temperature"
                    )
                ),
                vol.Optional(
                    CONF_OVERRIDE_HOURS, default=DEFAULT_OVERRIDE_HOURS
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=24,
                        step=0.5,
                        unit_of_measurement="h",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class HeatingPlanOptionsFlow(OptionsFlow):
    """Options of the whole home."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = Options.from_entry(self.config_entry)
        schema = vol.Schema(
            {
                vol.Optional(CONF_CALENDAR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar")
                ),
                vol.Required(CONF_HOLIDAYS_ENABLED): selector.BooleanSelector(),
                vol.Required(CONF_HOLIDAY_PROFILE): selector.TextSelector(),
                vol.Required(CONF_MIN_TEMP): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=15,
                        step=0.5,
                        unit_of_measurement="°C",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        suggested = {
            CONF_CALENDAR_ENTITY_ID: opts.calendar_entity_id,
            CONF_HOLIDAYS_ENABLED: opts.holidays_enabled,
            CONF_HOLIDAY_PROFILE: opts.holiday_profile,
            CONF_MIN_TEMP: opts.min_temp,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )
