"""Tests for the coordinators, entities and events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.parkstart.const import (
    DOMAIN,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    PLATES_SCAN_INTERVAL,
)

from .conftest import ENTRY_DATA, LOGIN_URL, SESSIONS_URL, Portal

# The recorded session runs 16:44-16:49 Amsterdam time (15:44-15:49 UTC).
NOW = datetime(2026, 10, 29, 15, 46, tzinfo=UTC)
SESSION_END = datetime(2026, 10, 29, 15, 49, tzinfo=UTC)

pytestmark = pytest.mark.freeze_time(NOW)


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity is not None, unique_id
    return entity


def state(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    return hass.states.get(entity_id(hass, platform, unique_id)).state


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, entry: MockConfigEntry, portal: Portal) -> None:
    """Apply the portal's current state and refresh the sessions coordinator."""
    portal.apply()
    await entry.runtime_data.sessions.async_refresh()
    await hass.async_block_till_done()


def new_session(session_id: int, plate: str, start: str, end: str) -> dict:
    return {
        "id": session_id,
        "permitId": 10001,
        "timeStartUtc": start,
        "timeEndUtc": end,
        "costMoney": 0.5,
        "lp": plate,
        "lpDescription": None,
        "isActive": True,
        "zoneCode": "BEZ_S-1",
    }


async def test_entities(hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal) -> None:
    await setup(hass, config_entry)

    active = hass.states.get(entity_id(hass, "sensor", "10001_active_sessions"))
    assert active.state == "1"
    [session] = active.attributes["sessions"]
    assert session["session_id"] == 3000001
    assert session["license_plate"] == "XX000X"
    assert session["start"] == "2026-10-29T16:44:00+01:00"

    parked = hass.states.get(entity_id(hass, "binary_sensor", "10001_XX000X_parked"))
    assert parked.state == STATE_ON
    assert parked.attributes["session_id"] == 3000001
    assert parked.attributes["cost"] == 0.03
    assert parked.attributes["zone"] == "BEZ_S-1"
    assert parked.attributes["license_plate"] == "XX000X"
    assert state(hass, "sensor", "10001_XX000X_session_start") == "2026-10-29T15:44:00+00:00"
    assert state(hass, "sensor", "10001_XX000X_session_end") == "2026-10-29T15:49:00+00:00"

    # A saved plate without a session: off and unknown, not unavailable.
    assert state(hass, "binary_sensor", "10001_YY111Y_parked") == STATE_OFF
    assert state(hass, "sensor", "10001_YY111Y_session_start") == STATE_UNKNOWN
    assert state(hass, "sensor", "10001_YY111Y_session_end") == STATE_UNKNOWN


async def test_devices(hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal) -> None:
    await setup(hass, config_entry)
    registry = dr.async_get(hass)

    permit = registry.async_get_device({(DOMAIN, "10001")})
    plate = registry.async_get_device({(DOMAIN, "10001_XX000X")})
    unnamed = registry.async_get_device({(DOMAIN, "10001_YY111Y")})
    assert plate.name == "Visitor A"
    assert plate.via_device_id == permit.id
    assert unnamed.name == "YY111Y"


async def test_no_started_event_for_existing_sessions(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    started = async_capture_events(hass, EVENT_SESSION_STARTED)
    await setup(hass, config_entry)
    assert started == []


async def test_session_ends_locally_at_end_time(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: Portal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup(hass, config_entry)
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)
    polls = portal.count("POST", SESSIONS_URL)

    freezer.move_to(SESSION_END)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert portal.count("POST", SESSIONS_URL) == polls
    assert state(hass, "binary_sensor", "10001_XX000X_parked") == STATE_OFF
    assert state(hass, "sensor", "10001_XX000X_session_end") == STATE_UNKNOWN
    assert state(hass, "sensor", "10001_active_sessions") == "0"
    [event] = ended
    assert event.data == {
        "session_id": 3000001,
        "license_plate": "XX000X",
        "label": "Visitor A",
        "permit_id": 10001,
        "start": "2026-10-29T16:44:00+01:00",
        "end": "2026-10-29T16:49:00+01:00",
        "cost": 0.03,
        "source": "external",
    }

    # The portal still lists it for a moment: no second event, still off.
    await poll(hass, config_entry, portal)
    assert len(ended) == 1
    assert state(hass, "binary_sensor", "10001_XX000X_parked") == STATE_OFF


async def test_events_from_polling(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    await setup(hass, config_entry)
    started = async_capture_events(hass, EVENT_SESSION_STARTED)
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)

    # Started in the app, for a plate that is not saved in the portal.
    portal.sessions.append(
        new_session(3000002, "ZZ999Z", "2026-10-29T16:45:00", "2026-10-29T18:00:00")
    )
    await poll(hass, config_entry, portal)
    assert [(e.data["session_id"], e.data["source"]) for e in started] == [(3000002, "external")]
    # Unsaved plates only show up in the permit's session list.
    assert state(hass, "sensor", "10001_active_sessions") == "2"

    # Stopped in the app.
    portal.sessions = [s for s in portal.sessions if s["id"] != 3000001]
    await poll(hass, config_entry, portal)
    assert [(e.data["session_id"], e.data["source"]) for e in ended] == [(3000001, "external")]
    assert state(hass, "binary_sensor", "10001_XX000X_parked") == STATE_OFF


async def test_event_source_home_assistant(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    await setup(hass, config_entry)
    started = async_capture_events(hass, EVENT_SESSION_STARTED)
    ended = async_capture_events(hass, EVENT_SESSION_ENDED)
    sessions = config_entry.runtime_data.sessions

    sessions.note_started_by_ha("YY111Y")
    portal.sessions.append(
        new_session(3000002, "YY111Y", "2026-10-29T16:46:00", "2026-10-29T18:00:00")
    )
    await poll(hass, config_entry, portal)
    sessions.note_stopped_by_ha(3000002)
    portal.sessions = [s for s in portal.sessions if s["id"] != 3000002]
    await poll(hass, config_entry, portal)

    assert [e.data["source"] for e in started] == ["home_assistant"]
    assert [e.data["source"] for e in ended] == ["home_assistant"]


async def test_plate_shows_active_session_over_later_one(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    portal.sessions.append(
        new_session(3000002, "XX000X", "2026-10-29T20:00:00", "2026-10-29T21:00:00")
    )
    portal.apply()
    await setup(hass, config_entry)

    parked = hass.states.get(entity_id(hass, "binary_sensor", "10001_XX000X_parked"))
    assert parked.attributes["session_id"] == 3000001


async def test_plates_mirror(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: Portal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup(hass, config_entry)
    registry = dr.async_get(hass)

    portal.plates = [
        {"licensePlate": "XX000X", "comment": "Renamed"},
        {"licensePlate": "AA123B", "comment": "New car"},
    ]
    portal.apply()
    freezer.tick(PLATES_SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert registry.async_get_device({(DOMAIN, "10001_XX000X")}).name == "Renamed"
    assert registry.async_get_device({(DOMAIN, "10001_AA123B")}).name == "New car"
    assert state(hass, "binary_sensor", "10001_AA123B_parked") == STATE_OFF
    assert registry.async_get_device({(DOMAIN, "10001_YY111Y")}) is None
    assert (
        er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "10001_YY111Y_parked")
        is None
    )
    # The permit device stays.
    assert registry.async_get_device({(DOMAIN, "10001")}) is not None


async def test_expired_session_relogs_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    await setup(hass, config_entry)
    portal.expire_session()

    await config_entry.runtime_data.sessions.async_refresh()

    assert config_entry.runtime_data.sessions.last_update_success
    assert portal.count("POST", LOGIN_URL) == 2


async def test_failed_relogin_starts_reauth_and_stops_logging_in(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    await setup(hass, config_entry)
    portal.login_ok = False
    portal.expire_session()
    runtime = config_entry.runtime_data

    await runtime.sessions.async_refresh()
    await runtime.plates.async_refresh()
    await hass.async_block_till_done()

    assert portal.count("POST", LOGIN_URL) == 2
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_options_set_poll_interval(hass: HomeAssistant, portal: Portal) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="u", data=ENTRY_DATA, options={"scan_interval": 3}
    )
    entry.add_to_hass(hass)
    await setup(hass, entry)
    assert entry.runtime_data.sessions.update_interval == timedelta(minutes=3)


async def test_plate_from_entity_template(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    """The README example: read a saved plate from its parked entity."""
    from homeassistant.helpers.template import Template

    await setup(hass, config_entry)

    template = Template("{{ state_attr('binary_sensor.visitor_a_parked', 'license_plate') }}", hass)
    assert template.async_render() == "XX000X"
    # A plate without a session still has the attribute.
    assert hass.states.get("binary_sensor.yy111y_parked").attributes["license_plate"] == "YY111Y"
