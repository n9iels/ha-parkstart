"""Tests for setting up and unloading a config entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parkstart.api import ParkStartAuthError, ParkStartConnectionError
from custom_components.parkstart.const import DOMAIN

from .conftest import ENTRY_DATA

LOGIN = "custom_components.parkstart.api.ParkStartClient.async_login"


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA)
    entry.add_to_hass(hass)

    with patch(LOGIN, new=AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (ParkStartAuthError, ConfigEntryState.SETUP_ERROR),
        (ParkStartConnectionError, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failure(
    hass: HomeAssistant, error: type[Exception], state: ConfigEntryState
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA)
    entry.add_to_hass(hass)

    with patch(LOGIN, new=AsyncMock(side_effect=error)) as login:
        await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is state
    login.assert_awaited_once()
    # A failed login starts the re-auth flow.
    reauth = [
        f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"
    ]
    assert bool(reauth) is (error is ParkStartAuthError)
