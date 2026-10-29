"""The ParkStart integration."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ParkStartAuthError, ParkStartClient, ParkStartError
from .const import CONF_MUNICIPALITY, DOMAIN, MUNICIPALITIES
from .coordinator import PlatesCoordinator, SessionsCoordinator
from .entity import permit_device_info, plate_device_id, plate_device_info
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class ParkStartData:
    """Runtime data for a config entry."""

    client: ParkStartClient
    plates: PlatesCoordinator
    sessions: SessionsCoordinator


type ParkStartConfigEntry = ConfigEntry[ParkStartData]


def create_client(hass: HomeAssistant, data: dict) -> ParkStartClient:
    """Create a client with its own session and cookie jar (not HA's shared session)."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return ParkStartClient(
        session, MUNICIPALITIES[data[CONF_MUNICIPALITY]], data[CONF_EMAIL], data[CONF_PASSWORD]
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions; they work for every loaded config entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ParkStartConfigEntry) -> bool:
    """Set up ParkStart from a config entry."""
    client = create_client(hass, dict(entry.data))
    try:
        await client.async_login()
    except ParkStartAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ParkStartError as err:
        raise ConfigEntryNotReady(str(err)) from err

    plates = PlatesCoordinator(hass, entry, client)
    sessions = SessionsCoordinator(hass, entry, client)
    await plates.async_config_entry_first_refresh()
    await sessions.async_config_entry_first_refresh()

    entry.runtime_data = ParkStartData(client=client, plates=plates, sessions=sessions)
    # Permit devices first: plate devices refer to them with via_device.
    registry = dr.async_get(hass)
    for permit in client.permits:
        registry.async_get_or_create(config_entry_id=entry.entry_id, **permit_device_info(permit))
    _sync_plate_devices(hass, entry)
    entry.async_on_unload(plates.async_add_listener(lambda: _sync_plate_devices(hass, entry)))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ParkStartConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ParkStartConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _sync_plate_devices(hass: HomeAssistant, entry: ParkStartConfigEntry) -> None:
    """Mirror the portal's plates: rename changed plates, remove deleted ones (with entities).

    New plates are added by the platforms.
    """
    plates = entry.runtime_data.plates.data
    if plates is None:
        return
    current = {
        plate_device_id(permit_id, plate.plate): plate_device_info(permit_id, plate)
        for permit_id, permit_plates in plates.items()
        for plate in permit_plates
    }
    permits = {str(permit.id) for permit in entry.runtime_data.client.permits}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        identifier = next((i for d, i in device.identifiers if d == DOMAIN), None)
        if identifier is None or identifier in permits:
            continue
        if (info := current.get(identifier)) is None:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
        elif device.name != info["name"]:
            registry.async_update_device(device.id, name=info["name"])
