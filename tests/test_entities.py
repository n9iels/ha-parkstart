"""Tests for the coordinators, entities and events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
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

from .conftest import (
    ACTIVE_SESSIONS,
    ENTRY_DATA,
    LOGIN_URL,
    SESSIONS_URL,
    VISITOR_A,
    YY111Y,
    Portal,
)

# The recorded session runs 16:44-16:49 Amsterdam time (15:44-15:49 UTC).
NOW = datetime(2026, 10, 29, 15, 46, tzinfo=UTC)
SESSION_END = datetime(2026, 10, 29, 15, 49, tzinfo=UTC)

pytestmark = pytest.mark.freeze_time(NOW)


def state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


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

    active = hass.states.get(ACTIVE_SESSIONS)
    assert active.state == "1"
    [session] = active.attributes["sessions"]
    assert session["session_id"] == 3000001
    assert session["license_plate"] == "XX000X"
    assert session["start"] == "2026-10-29T16:44:00+01:00"

    plate = hass.states.get(VISITOR_A)
    assert plate.state == STATE_ON
    assert plate.name == "ParkStart Sector 1 (Example) Visitor A"
    assert plate.attributes["license_plate"] == "XX000X"
    assert plate.attributes["session_id"] == 3000001
    assert plate.attributes["session_start"] == datetime(2026, 10, 29, 15, 44, tzinfo=UTC)
    assert plate.attributes["session_end"] == SESSION_END
    assert plate.attributes["cost"] == 0.03
    assert plate.attributes["zone"] == "BEZ_S-1"

    # A saved plate without a session: off, and the session attributes are empty.
    unnamed = hass.states.get(YY111Y)
    assert unnamed.state == STATE_OFF
    assert unnamed.attributes["license_plate"] == "YY111Y"
    assert unnamed.attributes["session_start"] is None
    assert unnamed.attributes["session_end"] is None


async def test_one_device_per_permit(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    await setup(hass, config_entry)

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    assert device.identifiers == {(DOMAIN, "10001")}
    assert device.name == "ParkStart Sector 1 (Example)"
    assert device.model == "Permit"
    entities = er.async_entries_for_device(er.async_get(hass), device.id)
    assert sorted(e.entity_id for e in entities) == sorted([VISITOR_A, YY111Y, ACTIVE_SESSIONS])


async def test_old_plate_devices_are_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal: Portal
) -> None:
    """Earlier versions made one device per plate, with three entities each."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    old = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "10001_XX000X")}
    )
    entities.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "10001_XX000X_parked",
        config_entry=config_entry,
        device_id=old.id,
        suggested_object_id="visitor_a_parked",
    )

    await setup(hass, config_entry)

    assert devices.async_get(old.id) is None
    assert entities.async_get("binary_sensor.visitor_a_parked") is None
    assert state(hass, VISITOR_A) == STATE_ON


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
    assert state(hass, VISITOR_A) == STATE_OFF
    assert hass.states.get(VISITOR_A).attributes["session_end"] is None
    assert state(hass, ACTIVE_SESSIONS) == "0"
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
    assert state(hass, VISITOR_A) == STATE_OFF


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
    assert state(hass, ACTIVE_SESSIONS) == "2"

    # Stopped in the app.
    portal.sessions = [s for s in portal.sessions if s["id"] != 3000001]
    await poll(hass, config_entry, portal)
    assert [(e.data["session_id"], e.data["source"]) for e in ended] == [(3000001, "external")]
    assert state(hass, VISITOR_A) == STATE_OFF


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

    assert hass.states.get(VISITOR_A).attributes["session_id"] == 3000001


async def test_plates_mirror(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: Portal,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup(hass, config_entry)
    registry = er.async_get(hass)

    portal.plates = [
        {"licensePlate": "XX000X", "comment": "Renamed"},
        {"licensePlate": "AA123B", "comment": "New car"},
    ]
    portal.apply()
    freezer.tick(PLATES_SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Renamed: same entity, new name.
    assert hass.states.get(VISITOR_A).name == "ParkStart Sector 1 (Example) Renamed"
    # Added and removed in the portal.
    assert state(hass, "binary_sensor.parkstart_sector_1_example_new_car") == STATE_OFF
    assert registry.async_get(YY111Y) is None
    assert hass.states.get(YY111Y) is None

    # A removed plate that comes back gets its entity again.
    portal.plates.append({"licensePlate": "YY111Y", "comment": ""})
    freezer.tick(PLATES_SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, YY111Y) == STATE_OFF


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
    """Templates can read the plate and session times from the entity."""
    from homeassistant.helpers.template import Template

    await setup(hass, config_entry)

    plate = Template(f"{{{{ state_attr('{VISITOR_A}', 'license_plate') }}}}", hass)
    assert plate.async_render() == "XX000X"
    end = Template(f"{{{{ state_attr('{VISITOR_A}', 'session_end') | as_timestamp }}}}", hass)
    assert end.async_render() == SESSION_END.timestamp()
