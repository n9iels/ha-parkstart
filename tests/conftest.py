"""Shared fixtures. All HTTP is mocked: no test ever talks to the live portal."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parkstart.const import CONF_MUNICIPALITY, DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://parkstart-gouda.parkpermit.eu"
LOGIN_URL = f"{BASE_URL}/"
PLATES_URL = f"{BASE_URL}/Customer/PlanSession/GetLicensePlates"
SESSIONS_URL = f"{BASE_URL}/Customer/SessionsOverview/GetParkingSessions/"

ENTRY_DATA = {
    CONF_MUNICIPALITY: "gouda",
    "email": "user@example.com",
    "password": "secret",
}


def load_fixture(name: str) -> str:
    """Load a recorded (anonymised) response."""
    return (FIXTURES / name).read_text()


def load_json(name: str) -> Any:
    """Load a recorded (anonymised) JSON response."""
    return json.loads(load_fixture(name))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components in every test."""
    return


class Portal:
    """A fake ParkStart portal on top of aioresponses. Responses can be changed mid-test."""

    def __init__(self, mock: aioresponses) -> None:
        self.mock = mock
        self.plates: list[dict[str, Any]] = load_json("license_plates.json")["licensePlates"]
        self.sessions: list[dict[str, Any]] = load_json("sessions.json")["data"]
        self.login_ok = True
        self.apply()

    def apply(self, clear: bool = True) -> None:
        """(Re)register all responses from the current state."""
        if clear:
            self.mock.clear()
        self.mock.get(
            LOGIN_URL, body=load_fixture("login_page.html"), content_type="text/html", repeat=True
        )
        if self.login_ok:
            self.mock.post(
                LOGIN_URL, status=302, headers={"Location": "/Customer/PlanSession"}, repeat=True
            )
        else:
            self.mock.post(
                LOGIN_URL,
                body=load_fixture("login_page.html"),
                content_type="text/html",
                repeat=True,
            )
        self.mock.get(
            f"{BASE_URL}/Customer/PlanSession",
            body=load_fixture("plan_session.html"),
            content_type="text/html",
            repeat=True,
        )
        self.mock.post(PLATES_URL, payload={"licensePlates": self.plates}, repeat=True)
        self.mock.post(SESSIONS_URL, payload={"data": self.sessions}, repeat=True)

    def expire_session(self) -> None:
        """Answer the next plates and sessions call with an expired-session redirect."""
        self.mock.clear()
        for url in (PLATES_URL, SESSIONS_URL):
            self.mock.post(url, status=302, headers={"Location": "/Error/Error"})
        self.apply(clear=False)

    def count(self, method: str, url: str) -> int:
        """Number of requests made to a URL."""
        return sum(
            len(calls)
            for (m, u), calls in self.mock.requests.items()
            if m == method and str(u) == url
        )


@pytest.fixture
def portal() -> Iterator[Portal]:
    """Mocked portal with the recorded fixtures."""
    with aioresponses() as mock:
        yield Portal(mock)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A ParkStart config entry added to hass."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="gouda_user@example.com", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    return entry
