"""Tests for the "parking session started/ended" device triggers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_get_device_automations,
    async_mock_service,
)

from custom_components.parkstart.const import DOMAIN

from .conftest import Portal

NOW = datetime(2026, 10, 29, 15, 46, tzinfo=UTC)
SESSION_END = datetime(2026, 10, 29, 15, 49, tzinfo=UTC)

pytestmark = pytest.mark.freeze_time(NOW)


@pytest.fixture
async def permit_device(hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal) -> str:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return dr.async_get(hass).async_get_device({(DOMAIN, "10001")}).id


async def add_automation(hass: HomeAssistant, trigger: dict[str, Any]) -> list[ServiceCall]:
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "triggers": trigger,
                "actions": {
                    "action": "test.automation",
                    "data": {
                        "plate": "{{ trigger.event.data.license_plate }}",
                        "source": "{{ trigger.event.data.source }}",
                    },
                },
            }
        },
    )
    return calls


def device_trigger(device_id: str, type_: str, **extra: str) -> dict[str, Any]:
    return {"platform": "device", "domain": DOMAIN, "device_id": device_id, "type": type_, **extra}


async def test_get_triggers(hass: HomeAssistant, permit_device: str) -> None:
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, permit_device)
    assert sorted(t["type"] for t in triggers if t["domain"] == DOMAIN) == [
        "session_ended",
        "session_started",
    ]


async def test_session_started(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal, permit_device: str
) -> None:
    calls = await add_automation(hass, device_trigger(permit_device, "session_started"))

    # Started in the app: seen on the next poll.
    portal.sessions.append({**portal.sessions[0], "id": 3000002, "lp": "ZZ999Z"})
    await config_entry.runtime_data.sessions.async_refresh()
    await hass.async_block_till_done()

    assert [c.data for c in calls] == [{"plate": "ZZ999Z", "source": "external"}]


async def test_session_started_by_home_assistant_only(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal, permit_device: str
) -> None:
    calls = await add_automation(
        hass, device_trigger(permit_device, "session_started", source="home_assistant")
    )

    portal.sessions.append({**portal.sessions[0], "id": 3000002, "lp": "ZZ999Z"})
    await config_entry.runtime_data.sessions.async_refresh()
    await hass.services.async_call(
        DOMAIN,
        "start_session",
        {"license_plate": "YY111Y", "duration": {"hours": 1}},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert [c.data for c in calls] == [{"plate": "YY111Y", "source": "home_assistant"}]


async def test_session_ended_at_end_time(
    hass: HomeAssistant, portal: Portal, permit_device: str, freezer: FrozenDateTimeFactory
) -> None:
    calls = await add_automation(hass, device_trigger(permit_device, "session_ended"))

    freezer.move_to(SESSION_END)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert [c.data for c in calls] == [{"plate": "XX000X", "source": "external"}]
