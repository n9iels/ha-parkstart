"""Binary sensors for the ParkStart integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ParkStartConfigEntry
from .api import LicensePlate
from .coordinator import SessionsCoordinator
from .entity import PlateEntity, setup_plate_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParkStartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ParkStart binary sensors."""

    def _plate_binary_sensors(
        coordinator: SessionsCoordinator, permit_id: int, plate: LicensePlate
    ) -> list[PlateEntity]:
        return [ParkedBinarySensor(coordinator, permit_id, plate, "parked")]

    setup_plate_entities(entry, async_add_entities, _plate_binary_sensors)


class ParkedBinarySensor(PlateEntity, BinarySensorEntity):
    """On while the plate has an active session."""

    @property
    def is_on(self) -> bool:
        """Whether the plate's session is running now."""
        session = self.session
        return session is not None and session.start <= dt_util.utcnow() < session.end

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The plate, and details of its session."""
        session = self.session
        return {
            "license_plate": self._plate,
            "session_id": session.id if session else None,
            "cost": session.cost if session else None,
            "zone": session.zone if session else None,
        }
