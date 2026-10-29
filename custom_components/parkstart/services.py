"""Actions for the ParkStart integration: parkstart.start_session and parkstart.stop_session."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .api import ParkingSession, ParkStartError, normalize_plate
from .const import CONF_MAX_DURATION, DEFAULT_MAX_DURATION, DOMAIN

if TYPE_CHECKING:
    from . import ParkStartConfigEntry

SERVICE_START_SESSION = "start_session"
SERVICE_STOP_SESSION = "stop_session"

ATTR_LICENSE_PLATE = "license_plate"
ATTR_DURATION = "duration"
ATTR_END_TIME = "end_time"
ATTR_PERMIT = "permit"
ATTR_SESSION_ID = "session_id"

START_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_LICENSE_PLATE): vol.All(cv.string, normalize_plate, cv.string),
            vol.Optional(ATTR_DURATION): cv.positive_time_period,
            vol.Optional(ATTR_END_TIME): cv.datetime,
            vol.Optional(ATTR_PERMIT): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_DURATION, ATTR_END_TIME),
    cv.has_at_most_one_key(ATTR_DURATION, ATTR_END_TIME),
)

STOP_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_SESSION_ID): vol.Coerce(int),
            vol.Optional(ATTR_LICENSE_PLATE): vol.All(cv.string, normalize_plate, cv.string),
            vol.Optional(ATTR_PERMIT): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_SESSION_ID, ATTR_LICENSE_PLATE),
    cv.has_at_most_one_key(ATTR_SESSION_ID, ATTR_LICENSE_PLATE),
)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _resolve_permit(hass: HomeAssistant, device_id: str | None) -> tuple[ParkStartConfigEntry, int]:
    """The config entry and permit id to use: from the permit device, or the only permit."""
    entries: list[ParkStartConfigEntry] = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if device_id is None:
        permits = [(e, p.id) for e in entries for p in e.runtime_data.client.permits]
        if len(permits) != 1:
            raise _error("permit_required")
        return permits[0]

    device = dr.async_get(hass).async_get(device_id)
    for entry in entries:
        if device is None or entry.entry_id not in device.config_entries:
            continue
        for permit in entry.runtime_data.client.permits:
            if (DOMAIN, str(permit.id)) in device.identifiers:
                return entry, permit.id
    raise _error("invalid_permit")


def _end_time(call: ServiceCall, now: datetime) -> datetime:
    if (duration := call.data.get(ATTR_DURATION)) is not None:
        return now + duration
    end: datetime = call.data[ATTR_END_TIME]
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt_util.get_default_time_zone())
    return end


async def _async_start_session(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    plate: str = call.data[ATTR_LICENSE_PLATE]
    entry, permit_id = _resolve_permit(hass, call.data.get(ATTR_PERMIT))
    max_duration = timedelta(
        hours=entry.options.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION.total_seconds() / 3600)
    )
    now = dt_util.utcnow()
    end = _end_time(call, now)
    if end <= now + timedelta(minutes=1):
        raise _error("end_too_soon")
    if end - now > max_duration:
        raise _error("too_long", max_hours=f"{max_duration.total_seconds() / 3600:g}")

    runtime = entry.runtime_data
    before = set(runtime.sessions.data or {})
    try:
        await runtime.client.async_start_session(permit_id, plate, end)
    except ParkStartError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="start_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    runtime.sessions.note_started_by_ha(plate)

    # The start call does not return the id: find the new session for this plate.
    await runtime.sessions.async_refresh()
    await runtime.plates.async_request_refresh()
    new = [
        s
        for s in (runtime.sessions.data or {}).values()
        if s.plate == plate and s.permit_id == permit_id and s.id not in before
    ]
    return {ATTR_SESSION_ID: new[0].id if len(new) == 1 else None}


async def _async_stop_session(call: ServiceCall) -> None:
    hass = call.hass
    session_id: int | None = call.data.get(ATTR_SESSION_ID)
    plate: str | None = call.data.get(ATTR_LICENSE_PLATE)
    if (device_id := call.data.get(ATTR_PERMIT)) is not None:
        entry, permit_id = _resolve_permit(hass, device_id)
        candidates = [(entry, permit_id)]
    else:
        candidates = [
            (e, None)
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]

    # Look in fresh data, so a session started elsewhere can be stopped right away.
    matches: list[tuple[ParkStartConfigEntry, ParkingSession]] = []
    for entry, permit_id in candidates:
        await entry.runtime_data.sessions.async_refresh()
        matches.extend(
            (entry, s)
            for s in (entry.runtime_data.sessions.data or {}).values()
            if (permit_id is None or s.permit_id == permit_id)
            and (s.id == session_id if session_id is not None else s.plate == plate)
        )
    if not matches:
        raise _error("session_not_found", session=str(session_id or plate))
    if len(matches) > 1:
        raise _error("multiple_sessions", plate=str(plate))

    entry, session = matches[0]
    runtime = entry.runtime_data
    try:
        await runtime.client.async_stop_session(session.id)
    except ParkStartError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="stop_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    runtime.sessions.note_stopped_by_ha(session.id)
    await runtime.sessions.async_refresh()
    await runtime.plates.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_SESSION,
        _async_start_session,
        schema=START_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_SESSION, _async_stop_session, schema=STOP_SCHEMA
    )
