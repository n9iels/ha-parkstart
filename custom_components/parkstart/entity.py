"""Base entities and helpers for the ParkStart integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import LicensePlate, ParkingSession, Permit
from .const import DOMAIN
from .coordinator import SessionsCoordinator

if TYPE_CHECKING:
    from . import ParkStartConfigEntry


def plate_device_id(permit_id: int, plate: str) -> str:
    """Device identifier of a plate device: (permit_id, plate)."""
    return f"{permit_id}_{plate}"


def permit_device_info(permit: Permit) -> DeviceInfo:
    """Device info for a permit."""
    return DeviceInfo(
        identifiers={(DOMAIN, str(permit.id))},
        name=f"{permit.product} {permit.zone_description or permit.zone or permit.id}",
        manufacturer="ParkStart",
        # Fixed, so the actions' permit selector can filter on it. The product is in the name.
        model="Permit",
        serial_number=str(permit.id),
    )


def plate_device_info(permit_id: int, plate: LicensePlate) -> DeviceInfo:
    """Device info for a saved plate, linked to its permit."""
    return DeviceInfo(
        identifiers={(DOMAIN, plate_device_id(permit_id, plate.plate))},
        name=plate.label,
        manufacturer="ParkStart",
        model="License plate",
        serial_number=plate.plate,
        via_device=(DOMAIN, str(permit_id)),
    )


def plate_session(
    sessions: Iterable[ParkingSession], permit_id: int, plate: str
) -> ParkingSession | None:
    """The session to show for a plate: the active one, otherwise the earliest one."""
    matches = sorted(
        (s for s in sessions if s.permit_id == permit_id and s.plate == plate),
        key=lambda s: s.start,
    )
    now = dt_util.utcnow()
    return next((s for s in matches if s.start <= now < s.end), None) or next(iter(matches), None)


def setup_plate_entities(
    entry: ParkStartConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[SessionsCoordinator, int, LicensePlate], Iterable[Entity]],
) -> None:
    """Add entities for every saved plate now, and for plates added in the portal later.

    Removed plates are cleaned up by removing their device (see __init__).
    """
    plates = entry.runtime_data.plates
    known: set[str] = set()

    @callback
    def _add_new_plates() -> None:
        new: list[Entity] = []
        for permit_id, permit_plates in (plates.data or {}).items():
            for plate in permit_plates:
                key = plate_device_id(permit_id, plate.plate)
                if key not in known:
                    known.add(key)
                    new.extend(factory(entry.runtime_data.sessions, permit_id, plate))
        if new:
            async_add_entities(new)

    _add_new_plates()
    entry.async_on_unload(plates.async_add_listener(_add_new_plates))


class PlateEntity(CoordinatorEntity[SessionsCoordinator]):
    """An entity on a plate device, showing that plate's session."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SessionsCoordinator, permit_id: int, plate: LicensePlate, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._permit_id = permit_id
        self._plate = plate.plate
        self._attr_translation_key = key
        self._attr_unique_id = f"{plate_device_id(permit_id, plate.plate)}_{key}"
        self._attr_device_info = plate_device_info(permit_id, plate)

    @property
    def session(self) -> ParkingSession | None:
        """This plate's session, if any."""
        return plate_session(self.coordinator.data.values(), self._permit_id, self._plate)
