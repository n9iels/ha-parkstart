"""The ParkStart integration."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ParkStartAuthError, ParkStartClient, ParkStartError
from .const import CONF_MUNICIPALITY, MUNICIPALITIES

# Sensor and binary_sensor platforms follow with the coordinator.
PLATFORMS: list[Platform] = []


@dataclass
class ParkStartData:
    """Runtime data for a config entry."""

    client: ParkStartClient


type ParkStartConfigEntry = ConfigEntry[ParkStartData]


def create_client(hass: HomeAssistant, data: dict) -> ParkStartClient:
    """Create a client with its own session and cookie jar (not HA's shared session)."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return ParkStartClient(
        session, MUNICIPALITIES[data[CONF_MUNICIPALITY]], data[CONF_EMAIL], data[CONF_PASSWORD]
    )


async def async_setup_entry(hass: HomeAssistant, entry: ParkStartConfigEntry) -> bool:
    """Set up ParkStart from a config entry."""
    client = create_client(hass, dict(entry.data))
    try:
        await client.async_login()
    except ParkStartAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ParkStartError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = ParkStartData(client=client)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ParkStartConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ParkStartConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
