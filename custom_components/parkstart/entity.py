"""Shared helpers for ParkStart entities."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .api import ParkingSession, Permit
from .const import DOMAIN


def plate_unique_id(permit_id: int, plate: str) -> str:
    """Unique id of a saved plate's entity: "<permit_id>_<plate>"."""
    return f"{permit_id}_{plate}"


def permit_device_info(permit: Permit) -> DeviceInfo:
    """Device info for a permit. Its saved plates are entities on this device."""
    return DeviceInfo(
        identifiers={(DOMAIN, str(permit.id))},
        name=f"ParkStart {permit.zone_description or permit.zone or permit.id}",
        manufacturer="ParkStart",
        # Fixed, so the actions' permit selector can filter on it.
        model="Permit",
        model_id=permit.product,
        serial_number=str(permit.id),
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
