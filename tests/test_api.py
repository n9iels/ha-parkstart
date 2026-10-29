"""Tests for the bundled API client."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.parkstart.api import (
    ParkStartAuthError,
    ParkStartClient,
    ParkStartError,
    format_api_time,
)
from custom_components.parkstart.const import API_TIMEZONE

from .conftest import BASE_URL, load_fixture

PLATES_URL = f"{BASE_URL}/Customer/PlanSession/GetLicensePlates"
SESSIONS_URL = f"{BASE_URL}/Customer/SessionsOverview/GetParkingSessions/"
START_URL = f"{BASE_URL}/Customer/PlanSession/StartParkingSession"
STOP_URL = f"{BASE_URL}/Customer/PlanSession/StopParkingSession"


def mock_login(mock: aioresponses) -> None:
    """Queue one successful login."""
    mock.get(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")
    mock.post(f"{BASE_URL}/", status=302, headers={"Location": "/Customer/PlanSession"}, body="")
    mock.get(
        f"{BASE_URL}/Customer/PlanSession",
        body=load_fixture("plan_session.html"),
        content_type="text/html",
    )


def mock_expired(mock: aioresponses, url: str) -> None:
    mock.post(url, status=302, headers={"Location": "/Error/Error"}, body="")


def requests_to(mock: aioresponses, method: str, url: str) -> list:
    return [
        call
        for (m, u), calls in mock.requests.items()
        if m == method and str(u) == url
        for call in calls
    ]


@pytest.fixture
async def client() -> AsyncIterator[ParkStartClient]:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        yield ParkStartClient(session, BASE_URL, "user@example.com", "secret")


@pytest.fixture
def mock() -> AsyncIterator[aioresponses]:
    with aioresponses() as m:
        yield m


async def test_login_loads_permits(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    await client.async_login()

    [permit] = client.permits
    assert permit.id == 10001
    assert permit.product == "Bezoeker bewoner"
    assert permit.zone == "BEZ_S-1"

    [login_post] = requests_to(mock, "POST", f"{BASE_URL}/")
    form = login_post.kwargs["data"]
    assert form["__RequestVerificationToken"] == "test-token-123"
    assert form["Email"] == "user@example.com"


async def test_login_failure(client: ParkStartClient, mock: aioresponses) -> None:
    mock.get(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")
    mock.post(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")

    with pytest.raises(ParkStartAuthError, match="Login failed"):
        await client.async_login()
    assert len(requests_to(mock, "POST", f"{BASE_URL}/")) == 1


async def test_login_captcha(client: ParkStartClient, mock: aioresponses) -> None:
    mock.get(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")
    mock.post(
        f"{BASE_URL}/", body=load_fixture("login_page_captcha.html"), content_type="text/html"
    )

    with pytest.raises(ParkStartAuthError, match="reCAPTCHA"):
        await client.async_login()


async def test_license_plates(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    mock.post(PLATES_URL, body=load_fixture("license_plates.json"), content_type="application/json")
    await client.async_login()

    plates = await client.async_get_license_plates(10001)

    assert [(p.plate, p.label) for p in plates] == [("XX000X", "Visitor A"), ("YY111Y", "YY111Y")]
    [call] = requests_to(mock, "POST", PLATES_URL)
    assert call.kwargs["json"] == {"permitId": 10001}
    assert call.kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


async def test_sessions_times_are_local(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    mock.post(SESSIONS_URL, body=load_fixture("sessions.json"), content_type="application/json")
    await client.async_login()

    [session] = await client.async_get_sessions()

    assert session.id == 3000001
    assert session.plate == "XX000X"
    assert session.cost == 0.03
    # 16:44 Amsterdam (CET) is 15:44 UTC.
    assert session.start == datetime(2026, 10, 29, 15, 44, tzinfo=UTC)
    assert session.end - session.start == timedelta(minutes=5)


async def test_expired_session_relogs_once(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    await client.async_login()
    mock_expired(mock, SESSIONS_URL)
    mock_login(mock)
    mock.post(SESSIONS_URL, body=load_fixture("sessions.json"), content_type="application/json")

    assert len(await client.async_get_sessions()) == 1
    assert len(requests_to(mock, "POST", f"{BASE_URL}/")) == 2
    assert len(requests_to(mock, "POST", SESSIONS_URL)) == 2


async def test_html_instead_of_json_counts_as_expired(
    client: ParkStartClient, mock: aioresponses
) -> None:
    mock_login(mock)
    await client.async_login()
    mock.post(SESSIONS_URL, body="<html></html>", content_type="text/html")
    mock_login(mock)
    mock.post(SESSIONS_URL, body=load_fixture("sessions.json"), content_type="application/json")

    assert len(await client.async_get_sessions()) == 1


async def test_start_session_body(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    mock.post(START_URL, payload={"successful": True})
    await client.async_login()
    end = datetime.now(API_TIMEZONE) + timedelta(hours=1)

    await client.async_start_session(10001, "xx-000-x", end)

    [call] = requests_to(mock, "POST", START_URL)
    body = call.kwargs["json"]
    assert body["lp"] == "XX000X"
    assert body["permitId"] == body["currentPermitId"] == 10001
    assert body["startedForUser"] == 20001
    assert body["comment"] == "C:\\path"
    assert body["saveMode"] is True
    assert body["timeEnd"] == format_api_time(end)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:00\.000Z", body["timeStart"])


async def test_start_session_is_never_retried(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    await client.async_login()
    mock_expired(mock, START_URL)

    with pytest.raises(ParkStartError, match="not retried"):
        await client.async_start_session(10001, "XX000X", datetime.now(UTC) + timedelta(hours=1))
    assert len(requests_to(mock, "POST", START_URL)) == 1
    assert len(requests_to(mock, "POST", f"{BASE_URL}/")) == 1


async def test_start_session_failure(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    mock.post(
        START_URL,
        payload={"successful": False, "notifications": [{"message": "Ongeldig kenteken"}]},
    )
    await client.async_login()

    with pytest.raises(ParkStartError, match="Ongeldig kenteken"):
        await client.async_start_session(10001, "XX000X", datetime.now(UTC) + timedelta(hours=1))


async def test_stop_session(client: ParkStartClient, mock: aioresponses) -> None:
    mock_login(mock)
    mock.post(STOP_URL, payload={"successful": True})
    await client.async_login()

    await client.async_stop_session(3000001)

    [call] = requests_to(mock, "POST", STOP_URL)
    assert call.kwargs["json"] == {"parkingSessionId": 3000001}


def test_format_api_time() -> None:
    local = datetime(2026, 10, 29, 16, 44, 37, 123, tzinfo=API_TIMEZONE)
    assert format_api_time(local) == "2026-10-29T15:44:00.000Z"
    assert json.dumps(format_api_time(local))


async def test_concurrent_expired_calls_log_in_once(
    client: ParkStartClient, mock: aioresponses
) -> None:
    mock_login(mock)
    await client.async_login()

    async def in_flight(url, **kwargs) -> None:
        # Keep the first request pending so both calls see the expired session.
        await asyncio.sleep(0.01)

    for url in (SESSIONS_URL, PLATES_URL):
        mock.post(url, status=302, headers={"Location": "/Error/Error"}, callback=in_flight)
    mock_login(mock)
    mock.post(SESSIONS_URL, body=load_fixture("sessions.json"), content_type="application/json")
    mock.post(PLATES_URL, body=load_fixture("license_plates.json"), content_type="application/json")

    await asyncio.gather(client.async_get_sessions(), client.async_get_license_plates(10001))

    assert len(requests_to(mock, "POST", f"{BASE_URL}/")) == 2


async def test_no_login_after_rejected_credentials(
    client: ParkStartClient, mock: aioresponses
) -> None:
    mock_login(mock)
    await client.async_login()
    mock_expired(mock, SESSIONS_URL)
    mock.get(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")
    mock.post(f"{BASE_URL}/", body=load_fixture("login_page.html"), content_type="text/html")
    with pytest.raises(ParkStartAuthError):
        await client.async_get_sessions()

    mock_expired(mock, PLATES_URL)
    with pytest.raises(ParkStartAuthError):
        await client.async_get_license_plates(10001)
    assert len(requests_to(mock, "POST", f"{BASE_URL}/")) == 2
