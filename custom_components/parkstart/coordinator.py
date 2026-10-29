"""Data update coordinators for the ParkStart integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LicensePlate, ParkingSession, ParkStartAuthError, ParkStartClient, ParkStartError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    PLATES_SCAN_INTERVAL,
    SOURCE_EXTERNAL,
    SOURCE_HOME_ASSISTANT,
)

if TYPE_CHECKING:
    from . import ParkStartConfigEntry

_LOGGER = logging.getLogger(__name__)


class PlatesCoordinator(DataUpdateCoordinator[dict[int, list[LicensePlate]]]):
    """Saved license plates per permit id."""

    config_entry: ParkStartConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ParkStartConfigEntry, client: ParkStartClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} plates",
            update_interval=PLATES_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, list[LicensePlate]]:
        try:
            return {
                permit.id: await self.client.async_get_license_plates(permit.id)
                for permit in self.client.permits
            }
        except ParkStartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ParkStartError as err:
            raise UpdateFailed(str(err)) from err


class SessionsCoordinator(DataUpdateCoordinator[dict[int, ParkingSession]]):
    """Active and planned sessions by session id.

    Fires started/ended events by diffing polls, and ends sessions locally at their end time
    so entities don't wait for the next poll.
    """

    config_entry: ParkStartConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ParkStartConfigEntry, client: ParkStartClient
    ) -> None:
        """Initialize the coordinator."""
        minutes = entry.options.get(CONF_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} sessions",
            update_interval=timedelta(minutes=minutes) if minutes else DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._end_timers: dict[int, CALLBACK_TYPE] = {}
        # Changes made by our own actions, so their events get source "home_assistant".
        self._started_by_ha: set[str] = set()
        self._stopped_by_ha: set[int] = set()

    @callback
    def note_started_by_ha(self, plate: str) -> None:
        """Mark the next new session for this plate as started by a parkstart action."""
        self._started_by_ha.add(plate)

    @callback
    def note_stopped_by_ha(self, session_id: int) -> None:
        """Mark this session as stopped by a parkstart action."""
        self._stopped_by_ha.add(session_id)

    async def _async_update_data(self) -> dict[int, ParkingSession]:
        try:
            sessions = await self.client.async_get_sessions()
        except ParkStartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ParkStartError as err:
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        # The portal may still list a session for a moment after its end time.
        new = {s.id: s for s in sessions if s.end > now}
        # The first poll is a baseline: sessions that already ran before HA started are not "started".
        if self.data is not None:
            for session_id, session in new.items():
                if session_id not in self.data:
                    self._fire(EVENT_SESSION_STARTED, session, self._started_source(session))
            for session_id, session in self.data.items():
                if session_id not in new:
                    self._fire(EVENT_SESSION_ENDED, session, self._ended_source(session_id))
        self._schedule_end_timers(new)
        return new

    def _started_source(self, session: ParkingSession) -> str:
        if session.plate in self._started_by_ha:
            self._started_by_ha.discard(session.plate)
            return SOURCE_HOME_ASSISTANT
        return SOURCE_EXTERNAL

    def _ended_source(self, session_id: int) -> str:
        if session_id in self._stopped_by_ha:
            self._stopped_by_ha.discard(session_id)
            return SOURCE_HOME_ASSISTANT
        return SOURCE_EXTERNAL

    def _schedule_end_timers(self, sessions: dict[int, ParkingSession]) -> None:
        for session_id in list(self._end_timers):
            if session_id not in sessions:
                self._end_timers.pop(session_id)()
        for session_id, session in sessions.items():
            if session_id in self._end_timers:
                self._end_timers.pop(session_id)()
            self._end_timers[session_id] = async_track_point_in_utc_time(
                self.hass, self._end_callback(session_id), session.end
            )

    def _end_callback(self, session_id: int) -> Callable[[datetime], None]:
        @callback
        def _ended(_now: datetime) -> None:
            self._end_timers.pop(session_id, None)
            if self.data is None or (session := self.data.get(session_id)) is None:
                return
            self._fire(EVENT_SESSION_ENDED, session, SOURCE_EXTERNAL)
            self.async_set_updated_data({k: v for k, v in self.data.items() if k != session_id})

        return _ended

    def _fire(self, event_type: str, session: ParkingSession, source: str) -> None:
        self.hass.bus.async_fire(event_type, session_event_data(session, source))

    async def async_shutdown(self) -> None:
        """Cancel the end-time timers."""
        await super().async_shutdown()
        for cancel in self._end_timers.values():
            cancel()
        self._end_timers.clear()


def session_event_data(session: ParkingSession, source: str) -> dict[str, Any]:
    """Event payload for parkstart_session_started / parkstart_session_ended."""
    return {
        "session_id": session.id,
        "license_plate": session.plate,
        "label": session.label,
        "permit_id": session.permit_id,
        "start": session.start.isoformat(),
        "end": session.end.isoformat(),
        "cost": session.cost,
        "source": source,
    }
