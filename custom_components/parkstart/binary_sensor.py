"""Binary sensors for the ParkStart integration: one per saved license plate."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ParkStartConfigEntry
from .api import LicensePlate, ParkingSession, Permit
from .coordinator import PlatesCoordinator, SessionsCoordinator
from .entity import permit_device_info, plate_session, plate_unique_id

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ParkStartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an entity for every saved plate now, and for plates added in the portal later.

    Removed plates are cleaned up in __init__.
    """
    runtime = entry.runtime_data
    permits = {p.id: p for p in runtime.client.permits}
    known: set[str] = set()

    @callback
    def _add_new_plates() -> None:
        current = {
            plate_unique_id(permit_id, plate.plate): (permit_id, plate)
            for permit_id, plates in (runtime.plates.data or {}).items()
            for plate in plates
        }
        # Forget removed plates, so they are added again if they come back.
        known.intersection_update(current)
        new = [
            LicensePlateBinarySensor(runtime.sessions, runtime.plates, permits[permit_id], plate)
            for unique_id, (permit_id, plate) in current.items()
            if unique_id not in known and permit_id in permits
        ]
        known.update(e.unique_id for e in new)
        if new:
            async_add_entities(new)

    _add_new_plates()
    entry.async_on_unload(runtime.plates.async_add_listener(_add_new_plates))


class LicensePlateBinarySensor(CoordinatorEntity[SessionsCoordinator], BinarySensorEntity):
    """A saved license plate, on while it has an active session."""

    _attr_has_entity_name = True
    _attr_translation_key = "license_plate"

    def __init__(
        self,
        coordinator: SessionsCoordinator,
        plates: PlatesCoordinator,
        permit: Permit,
        plate: LicensePlate,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._plates = plates
        self._permit_id = permit.id
        self._plate = plate.plate
        self._label = plate.label
        self._attr_unique_id = plate_unique_id(permit.id, plate.plate)
        self._attr_device_info = permit_device_info(permit)

    async def async_added_to_hass(self) -> None:
        """Also follow the plates, so a renamed label shows up."""
        await super().async_added_to_hass()
        self.async_on_remove(self._plates.async_add_listener(self._handle_plates_update))

    @callback
    def _handle_plates_update(self) -> None:
        for plate in (self._plates.data or {}).get(self._permit_id, []):
            if plate.plate == self._plate and plate.label != self._label:
                self._label = plate.label
                self.async_write_ha_state()

    @property
    def name(self) -> str:
        """The plate's label in the portal, falling back to the plate."""
        return self._label

    @property
    def session(self) -> ParkingSession | None:
        """This plate's session, if any."""
        return plate_session(self.coordinator.data.values(), self._permit_id, self._plate)

    @property
    def is_on(self) -> bool:
        """Whether the plate's session is running now."""
        session = self.session
        return session is not None and session.start <= dt_util.utcnow() < session.end

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The plate, and details of its session (None without a session)."""
        session = self.session
        return {
            "license_plate": self._plate,
            "session_id": session.id if session else None,
            "session_start": session.start if session else None,
            "session_end": session.end if session else None,
            "cost": session.cost if session else None,
            "zone": session.zone if session else None,
        }
