"""Sensors for the ParkStart integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ParkStartConfigEntry
from .api import ParkingSession, Permit
from .coordinator import SessionsCoordinator
from .entity import permit_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParkStartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ParkStart sensors."""
    sessions = entry.runtime_data.sessions
    async_add_entities(
        ActiveSessionsSensor(sessions, permit) for permit in entry.runtime_data.client.permits
    )


class ActiveSessionsSensor(CoordinatorEntity[SessionsCoordinator], SensorEntity):
    """Number of active sessions on a permit, with the session list as an attribute.

    This includes sessions for plates that are not saved in the portal.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "active_sessions"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SessionsCoordinator, permit: Permit) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._permit_id = permit.id
        self._attr_unique_id = f"{permit.id}_active_sessions"
        self._attr_device_info = permit_device_info(permit)

    @property
    def _sessions(self) -> list[ParkingSession]:
        return sorted(
            (s for s in self.coordinator.data.values() if s.permit_id == self._permit_id),
            key=lambda s: s.start,
        )

    @property
    def native_value(self) -> int:
        """Number of sessions."""
        return len(self._sessions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The full session list."""
        return {
            "sessions": [
                {
                    "session_id": s.id,
                    "license_plate": s.plate,
                    "label": s.label,
                    "start": s.start.isoformat(),
                    "end": s.end.isoformat(),
                    "cost": s.cost,
                    "zone": s.zone,
                }
                for s in self._sessions
            ]
        }
