"""Shared fixtures. All HTTP is mocked: no test ever talks to the live portal."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.parkstart.const import CONF_MUNICIPALITY

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://parkstart-gouda.parkpermit.eu"

ENTRY_DATA = {
    CONF_MUNICIPALITY: "gouda",
    "email": "user@example.com",
    "password": "secret",
}


def load_fixture(name: str) -> str:
    """Load a recorded (anonymised) response."""
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components in every test."""
    return
