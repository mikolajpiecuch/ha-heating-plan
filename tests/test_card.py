"""Tests for serving the Lovelace card."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.heating_plan import CARD_PATH, CARD_URL, async_register_card


async def test_card_file_exists() -> None:
    text = CARD_PATH.read_text(encoding="utf-8")
    assert 'customElements.define("heating-plan-card"' in text
    assert "heating_plan/rooms" in text
    assert "heating_plan/set_plan" in text


async def test_card_is_registered(hass: HomeAssistant) -> None:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components.add("frontend")
    with patch(
        "homeassistant.components.frontend.add_extra_js_url"
    ) as add_extra_js_url:
        await async_register_card(hass)
    [paths] = hass.http.async_register_static_paths.call_args.args
    assert paths[0].url_path == CARD_URL
    assert paths[0].path == str(CARD_PATH)
    url = add_extra_js_url.call_args.args[1]
    assert url.startswith(f"{CARD_URL}?v=")


async def test_no_frontend_no_card(hass: HomeAssistant) -> None:
    hass.http = None
    await async_register_card(hass)
