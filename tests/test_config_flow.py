"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parkstart.api import ParkStartAuthError, ParkStartConnectionError
from custom_components.parkstart.const import CONF_MAX_DURATION, CONF_SCAN_INTERVAL, DOMAIN

from .conftest import ENTRY_DATA

LOGIN = "custom_components.parkstart.api.ParkStartClient.async_login"


@pytest.fixture(autouse=True)
def no_setup():
    """Don't set the entry up after the flow; that is tested elsewhere."""
    with patch("custom_components.parkstart.async_setup_entry", return_value=True):
        yield


async def test_user_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    with patch(LOGIN, new=AsyncMock()) as login:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == "gouda_user@example.com"
    login.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "key"),
    [(ParkStartAuthError, "invalid_auth"), (ParkStartConnectionError, "cannot_connect")],
)
async def test_user_flow_errors(hass: HomeAssistant, error: type[Exception], key: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(LOGIN, new=AsyncMock(side_effect=error)) as login:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    login.assert_awaited_once()


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA).add_to_hass(
        hass
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(LOGIN, new=AsyncMock()) as login:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    login.assert_not_awaited()


async def test_reauth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(LOGIN, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "new-secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-secret"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_MAX_DURATION: 4}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 5, CONF_MAX_DURATION: 4}
