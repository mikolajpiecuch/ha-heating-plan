"""Tests for the config flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.heating_plan.const import DOMAIN


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The user step creates an entry with the given name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Dom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dom"


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A created entry loads and unloads."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Dom"}
    )
    entry = result["result"]
    assert entry.state is config_entries.ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
