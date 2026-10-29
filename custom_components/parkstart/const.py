"""Constants for the ParkStart integration."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "parkstart"

CONF_MUNICIPALITY = "municipality"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MAX_DURATION = "max_duration"

# Fixed list: only municipalities that have been checked against the API.
MUNICIPALITIES: dict[str, str] = {
    "gouda": "https://parkstart-gouda.parkpermit.eu",
}

DEFAULT_SCAN_INTERVAL = timedelta(minutes=10)
DEFAULT_MAX_DURATION = timedelta(hours=24)
PLATES_SCAN_INTERVAL = timedelta(minutes=60)

# The API returns session times as naive local times in this zone.
API_TIMEZONE = ZoneInfo("Europe/Amsterdam")
