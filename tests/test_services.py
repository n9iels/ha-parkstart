"""Tests for the parkstart.start_session and parkstart.stop_session actions.

The portal is mocked: no test ever starts a real (paid) session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.parkstart.const import DOMAIN, EVENT_SESSION_ENDED, EVENT_SESSION_STARTED

from .conftest import ACTIVE_SESSIONS, ENTRY_DATA, LOGIN_URL, START_URL, VISITOR_A, YY111Y, Portal

NOW = datetime(2026, 10, 29, 15, 46, tzinfo=UTC)

pytestmark = pytest.mark.freeze_time(NOW)


@pytest.fixture
async def loaded(hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def start(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, "start_session", data, blocking=True, return_response=True
    )


async def stop(hass: HomeAssistant, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, "stop_session", data, blocking=True)


def parked(hass: HomeAssistant, plate: str) -> str:
    return hass.states.get({"XX000X": VISITOR_A, "YY111Y": YY111Y}[plate]).state


@pytest.mark.usefixtures("loaded")
async def test_start_with_duration(hass: HomeAssistant, portal: Portal) -> None:
    started = async_capture_events(hass, EVENT_SESSION_STARTED)

    response = await start(hass, license_plate="yy-111-y", duration={"hours": 1})

    assert response == {"session_id": 3000101}
    [body] = portal.started
    assert body["lp"] == "YY111Y"
    assert body["permitId"] == 10001
    assert body["timeStart"] == "2026-10-29T15:46:00.000Z"
    assert body["timeEnd"] == "2026-10-29T16:46:00.000Z"
    assert [(e.data["session_id"], e.data["source"]) for e in started] == [
        (3000101, "home_assistant")
    ]
    assert parked(hass, "YY111Y") == STATE_ON


@pytest.mark.usefixtures("loaded")
async def test_start_with_end_time_in_local_time(hass: HomeAssistant, portal: Portal) -> None:
    await hass.config.async_set_time_zone("Europe/Amsterdam")

    await start(hass, license_plate="ZZ999Z", end_time="2026-10-29 18:30:00")

    # Any plate can be started, not only saved ones.
    [body] = portal.started
    assert body["lp"] == "ZZ999Z"
    assert body["timeEnd"] == "2026-10-29T17:30:00.000Z"


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    "data",
    [
        {"license_plate": "YY111Y"},
        {"license_plate": "YY111Y", "duration": {"hours": 1}, "end_time": "2026-10-29 18:30:00"},
    ],
)
async def test_start_needs_exactly_one_of_duration_and_end_time(
    hass: HomeAssistant, portal: Portal, data: dict[str, Any]
) -> None:
    with pytest.raises(vol.Invalid):
        await start(hass, **data)
    assert portal.started == []


async def test_start_longer_than_max_is_refused(hass: HomeAssistant, portal: Portal) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="u", data=ENTRY_DATA, options={"max_duration": 2}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await start(hass, license_plate="YY111Y", duration={"hours": 3})
    assert err.value.translation_key == "too_long"
    assert portal.started == []

    await start(hass, license_plate="YY111Y", duration={"hours": 2})
    assert len(portal.started) == 1


@pytest.mark.usefixtures("loaded")
async def test_start_end_time_in_past_is_refused(hass: HomeAssistant, portal: Portal) -> None:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    with pytest.raises(ServiceValidationError) as err:
        await start(hass, license_plate="YY111Y", end_time="2026-10-29 15:00:00")
    assert err.value.translation_key == "end_too_soon"
    assert portal.started == []


@pytest.mark.usefixtures("loaded")
async def test_start_failure(hass: HomeAssistant, portal: Portal) -> None:
    started = async_capture_events(hass, EVENT_SESSION_STARTED)
    portal.start_response = {"successful": False, "notifications": [{"message": "Ongeldig"}]}

    with pytest.raises(HomeAssistantError, match="Ongeldig"):
        await start(hass, license_plate="YY111Y", duration={"hours": 1})
    assert len(portal.started) == 1
    assert started == []


@pytest.mark.usefixtures("loaded")
async def test_start_logs_in_again_after_expired_session(
    hass: HomeAssistant, portal: Portal
) -> None:
    portal.expire_session(START_URL)

    response = await start(hass, license_plate="YY111Y", duration={"hours": 1})

    assert response == {"session_id": 3000101}
    assert portal.count("POST", LOGIN_URL) == 2
    assert portal.count("POST", START_URL) == 2
    # The redirected call was not processed: only one session was started.
    assert len(portal.started) == 1


@pytest.mark.usefixtures("loaded")
async def test_stop_by_plate(hass: HomeAssistant, portal: Portal) -> None:
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)

    await stop(hass, license_plate="xx-000-x")

    assert portal.stopped == [3000001]
    assert [(e.data["session_id"], e.data["source"]) for e in ended] == [
        (3000001, "home_assistant")
    ]
    assert parked(hass, "XX000X") == STATE_OFF


@pytest.mark.usefixtures("loaded")
async def test_stop_by_session_id(hass: HomeAssistant, portal: Portal) -> None:
    await stop(hass, session_id="3000001")
    assert portal.stopped == [3000001]


@pytest.mark.usefixtures("loaded")
async def test_stop_session_started_elsewhere_since_last_poll(
    hass: HomeAssistant, portal: Portal
) -> None:
    portal.sessions.append({**portal.sessions[0], "id": 3000002, "lp": "ZZ999Z"})
    await stop(hass, license_plate="ZZ999Z")
    assert portal.stopped == [3000002]


@pytest.mark.usefixtures("loaded")
async def test_stop_plate_with_two_sessions_is_refused(hass: HomeAssistant, portal: Portal) -> None:
    portal.sessions.append({**portal.sessions[0], "id": 3000002})

    with pytest.raises(ServiceValidationError) as err:
        await stop(hass, license_plate="XX000X")
    assert err.value.translation_key == "multiple_sessions"
    assert portal.stopped == []


@pytest.mark.usefixtures("loaded")
async def test_stop_unknown_session(hass: HomeAssistant, portal: Portal) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await stop(hass, session_id=123)
    assert err.value.translation_key == "session_not_found"
    assert portal.stopped == []


@pytest.mark.usefixtures("loaded")
async def test_permit_device(hass: HomeAssistant, portal: Portal) -> None:
    registry = dr.async_get(hass)
    permit = registry.async_get_device({(DOMAIN, "10001")})

    await start(hass, license_plate="YY111Y", duration={"hours": 1}, permit=permit.id)
    assert portal.started[0]["permitId"] == 10001

    with pytest.raises(ServiceValidationError) as err:
        await start(hass, license_plate="YY111Y", duration={"hours": 1}, permit="not-a-permit")
    assert err.value.translation_key == "invalid_permit"
    assert len(portal.started) == 1


async def test_permit_required_without_loaded_entry(hass: HomeAssistant, portal: Portal) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="u", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await start(hass, license_plate="YY111Y", duration={"hours": 1})
    assert err.value.translation_key == "permit_required"
    assert portal.started == []


def device(hass: HomeAssistant, identifier: str) -> str:
    found = dr.async_get(hass).async_get_device({(DOMAIN, identifier)})
    assert found is not None, identifier
    return found.id


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    "target",
    [
        {"entity_id": YY111Y},
        {"entity_id": [YY111Y]},
    ],
)
async def test_start_by_target_entity(
    hass: HomeAssistant, portal: Portal, target: dict[str, Any]
) -> None:
    response = await start(hass, duration={"hours": 1}, **target)

    assert response == {"session_id": 3000101}
    [body] = portal.started
    assert body["lp"] == "YY111Y"
    assert body["permitId"] == 10001
    assert parked(hass, "YY111Y") == STATE_ON


@pytest.mark.usefixtures("loaded")
async def test_stop_by_target(hass: HomeAssistant, portal: Portal) -> None:
    await stop(hass, entity_id=VISITOR_A)

    assert portal.stopped == [3000001]
    assert parked(hass, "XX000X") == STATE_OFF


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    ("target", "key"),
    [
        (
            {"entity_id": [VISITOR_A, YY111Y]},
            "one_plate_target",
        ),
        # The permit device holds both plates.
        ({"device_id": "permit"}, "one_plate_target"),
        (
            {"entity_id": ACTIVE_SESSIONS},
            "invalid_plate_entity",
        ),
        ({"entity_id": "binary_sensor.does_not_exist"}, "invalid_plate_entity"),
    ],
)
async def test_invalid_target(
    hass: HomeAssistant, portal: Portal, target: dict[str, Any], key: str
) -> None:
    if target.get("device_id") == "permit":
        target = {"device_id": device(hass, "10001")}
    for entity_id in cv.ensure_list(target.get("entity_id")):
        # Only the made-up entity may be missing; the others must fail for not being a plate.
        assert entity_id == "binary_sensor.does_not_exist" or hass.states.get(entity_id)
    with pytest.raises(ServiceValidationError) as err:
        await start(hass, duration={"hours": 1}, **target)
    assert err.value.translation_key == key
    with pytest.raises(ServiceValidationError):
        await stop(hass, **target)
    assert portal.started == []
    assert portal.stopped == []


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    ("data", "key"),
    [
        (
            {"entity_id": YY111Y, "license_plate": "YY111Y"},
            "plate_or_target",
        ),
        ({}, "plate_or_target"),
        ({"entity_id": YY111Y, "permit": "abc"}, "permit_with_target"),
    ],
)
async def test_start_target_plate_and_permit_rules(
    hass: HomeAssistant, portal: Portal, data: dict[str, Any], key: str
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await start(hass, duration={"hours": 1}, **data)
    assert err.value.translation_key == key
    assert portal.started == []


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    "data",
    [
        {},
        {"entity_id": VISITOR_A, "session_id": 3000001},
        {"license_plate": "XX000X", "session_id": 3000001},
    ],
)
async def test_stop_needs_exactly_one_way_to_pick(
    hass: HomeAssistant, portal: Portal, data: dict[str, Any]
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await stop(hass, **data)
    assert err.value.translation_key == "plate_target_or_session"
    assert portal.stopped == []
