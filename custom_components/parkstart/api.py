"""Async client for the (unofficial) ParkStart parking portal API.

See docs/API.md for the reverse-engineered endpoints. Rules that this client enforces:
- Logins are never retried: failed logins enable a reCAPTCHA.
- An expired session leads to one re-login and one retry of the call.
- Starting a session costs money. It is only retried after an expired-session redirect, which means
  the start was not processed. Network errors and unexpected HTML answers are never retried.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import API_TIMEZONE

_TOKEN_RE = re.compile(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"')
_MODEL_RE = re.compile(r"planSession\.init\('(.*?)'\);", re.DOTALL)
_CAPTCHA_RE = re.compile(r'name="ShowCaptcha"[^>]*value="True"', re.IGNORECASE)

_PLAN_SESSION = "/Customer/PlanSession"
_SESSIONS_OVERVIEW = "/Customer/SessionsOverview/Index/"

# DevExtreme load options the sessions overview page sends.
_SESSIONS_FILTER = {
    "skip": 0,
    "take": 50,
    "requireTotalCount": "true",
    "cstId": 0,
    "vstId": 0,
    "searchString": "",
    "startIndex": 0,
    "pageSize": 0,
    "sortColumn": "",
    "sortDirection": "",
    "totalRows": 0,
    "pageCount": -2147483648,
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ParkStartError(Exception):
    """Base error for the ParkStart API."""


class ParkStartConnectionError(ParkStartError):
    """The portal could not be reached or returned something unexpected."""


class ParkStartAuthError(ParkStartError):
    """Logging in failed (wrong credentials or a reCAPTCHA is required)."""


class _SessionExpiredError(ParkStartError):
    """The portal session expired; a re-login is needed."""

    def __init__(self, message: str, *, redirected: bool) -> None:
        super().__init__(message)
        # A redirect means the request was not processed; an HTML page might come after processing.
        self.redirected = redirected


@dataclass(frozen=True, slots=True)
class Permit:
    """A parking permit on the account."""

    id: int
    product: str
    zone: str | None
    zone_description: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Permit:
        """Create from a `permitList` entry."""
        return cls(
            id=int(data["id"]),
            product=data.get("permitProduct") or str(data["id"]),
            zone=data.get("paidParkingAreaId"),
            zone_description=data.get("paidParkingAreaIdDescription"),
        )


@dataclass(frozen=True, slots=True)
class LicensePlate:
    """A license plate saved in the portal."""

    plate: str
    comment: str | None

    @property
    def label(self) -> str:
        """The user's label for the plate, falling back to the plate itself."""
        return self.comment or self.plate

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> LicensePlate:
        """Create from a `licensePlates` entry."""
        return cls(plate=normalize_plate(data["licensePlate"]), comment=data.get("comment") or None)


@dataclass(frozen=True, slots=True)
class ParkingSession:
    """An active or planned parking session."""

    id: int
    permit_id: int
    plate: str
    label: str | None
    start: datetime
    end: datetime
    cost: float | None
    zone: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ParkingSession:
        """Create from a GetParkingSessions `data` entry."""
        return cls(
            id=int(data["id"]),
            permit_id=int(data["permitId"]),
            plate=normalize_plate(data["lp"]),
            label=data.get("lpDescription") or None,
            start=parse_api_time(data["timeStartUtc"]),
            end=parse_api_time(data["timeEndUtc"]),
            cost=data.get("costMoney"),
            zone=data.get("zoneCode"),
        )


def normalize_plate(plate: str) -> str:
    """Upper case, without dashes or spaces."""
    return plate.upper().replace("-", "").replace(" ", "")


def parse_api_time(value: str) -> datetime:
    """Parse an API time. Despite the `Utc` suffix in the field names, they are local times."""
    return datetime.fromisoformat(value).replace(tzinfo=API_TIMEZONE)


def format_api_time(value: datetime) -> str:
    """Format a time for the API: UTC ISO-8601 with `Z`, seconds zeroed."""
    value = value.astimezone(UTC).replace(second=0, microsecond=0)
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ParkStartClient:
    """Client for one ParkStart account.

    The session must have its own cookie jar: the portal session lives in cookies.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, email: str, password: str
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._model: dict[str, Any] | None = None
        # Serialises logins so concurrent calls that hit an expired session log in only once.
        self._login_lock = asyncio.Lock()
        self._login_attempts = 0
        self._login_error: ParkStartError | None = None

    @property
    def permits(self) -> list[Permit]:
        """Permits on the account, as loaded at login."""
        if self._model is None:
            raise ParkStartError("Not logged in")
        return [Permit.from_api(p) for p in self._model.get("permitList", [])]

    async def async_login(self) -> None:
        """Log in. Exactly one attempt; never call this in a retry loop."""
        async with self._login_lock:
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        # After rejected credentials, only a new client (re-auth) may log in again.
        if isinstance(self._login_error, ParkStartAuthError):
            raise self._login_error
        self._login_attempts += 1
        self._login_error = None
        try:
            await self._async_do_login()
        except ParkStartError as err:
            self._login_error = err
            raise

    async def _async_do_login(self) -> None:
        # Start from an empty jar: GET / while logged in logs the session out anyway.
        self._session.cookie_jar.clear()
        login_page = await self._request("GET", "/")
        if not (match := _TOKEN_RE.search(login_page)):
            raise ParkStartConnectionError("Login page has no verification token")
        form = {
            "__RequestVerificationToken": match.group(1),
            "Email": self._email,
            "Password": self._password,
            "ReturnUrl": "",
            "ShowCaptcha": "False",
            "ScreenWidth": "1440",
            "ScreenHeight": "900",
        }
        try:
            async with self._session.post(
                f"{self._base_url}/", data=form, allow_redirects=False, timeout=_TIMEOUT
            ) as resp:
                location = resp.headers.get("Location", "")
                body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ParkStartConnectionError(f"Login request failed: {err}") from err
        if resp.status != 302 or not location.rstrip("/").endswith(_PLAN_SESSION):
            if _CAPTCHA_RE.search(body):
                raise ParkStartAuthError("Login requires a reCAPTCHA; log in once in a browser")
            raise ParkStartAuthError("Login failed")
        await self._async_load_model()

    async def _async_load_model(self) -> None:
        """Load the PlanSession view model (permits and the start-session template)."""
        page = await self._request("GET", _PLAN_SESSION)
        if not (match := _MODEL_RE.search(page)):
            raise ParkStartConnectionError("PlanSession page has no view model")
        raw = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        try:
            self._model = json.loads(raw)
        except ValueError as err:
            raise ParkStartConnectionError("Could not parse the PlanSession view model") from err

    async def async_get_license_plates(self, permit_id: int) -> list[LicensePlate]:
        """Saved license plates for a permit."""
        data = await self._ajax(f"{_PLAN_SESSION}/GetLicensePlates", {"permitId": permit_id})
        return [LicensePlate.from_api(p) for p in data.get("licensePlates") or []]

    async def async_get_sessions(self) -> list[ParkingSession]:
        """Active and planned sessions for all permits. Ended sessions are not listed."""
        data = await self._ajax(
            "/Customer/SessionsOverview/GetParkingSessions/",
            _SESSIONS_FILTER,
            referer=_SESSIONS_OVERVIEW,
            form=True,
        )
        return [ParkingSession.from_api(s) for s in data.get("data") or []]

    async def async_start_session(self, permit_id: int, plate: str, end: datetime) -> None:
        """Start a session now. Costs money: retried only after an expired-session redirect."""
        if self._model is None:
            raise ParkStartError("Not logged in")
        body = dict(self._model["addItem"])
        body.update(
            {
                "permitId": permit_id,
                "currentPermitId": permit_id,
                "lp": normalize_plate(plate),
                "timeStart": format_api_time(datetime.now(UTC)),
                "timeEnd": format_api_time(end),
                "saveMode": True,
            }
        )
        result = await self._ajax(
            f"{_PLAN_SESSION}/StartParkingSession", body, retry_after_html=False
        )
        _raise_unless_successful(result, "Starting the session failed")

    async def async_stop_session(self, session_id: int) -> None:
        """Stop (or cancel) a session."""
        result = await self._ajax(
            f"{_PLAN_SESSION}/StopParkingSession",
            {"parkingSessionId": int(session_id)},
            referer=_SESSIONS_OVERVIEW,
        )
        _raise_unless_successful(result, "Stopping the session failed")

    async def _ajax(
        self,
        path: str,
        body: dict[str, Any],
        *,
        referer: str = _PLAN_SESSION,
        form: bool = False,
        retry_after_html: bool = True,
    ) -> dict[str, Any]:
        """Call a JSON endpoint; on an expired session log in once and retry once."""
        attempts = self._login_attempts
        try:
            return await self._ajax_once(path, body, referer=referer, form=form)
        except _SessionExpiredError as err:
            if not err.redirected and not retry_after_html:
                raise ParkStartError(
                    f"Unexpected response from {path}; not retried, check the portal"
                ) from err
        async with self._login_lock:
            if self._login_attempts == attempts:
                await self._async_login_locked()
            elif self._login_error is not None:
                # Another call already tried to log in meanwhile, and it failed.
                raise self._login_error
        try:
            return await self._ajax_once(path, body, referer=referer, form=form)
        except _SessionExpiredError as err:
            raise ParkStartConnectionError("Portal session expired right after login") from err

    async def _ajax_once(
        self, path: str, body: dict[str, Any], *, referer: str, form: bool
    ) -> dict[str, Any]:
        # Without these headers the server answers 302 -> /Error/Error.
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self._base_url,
            "Referer": f"{self._base_url}{referer}",
        }
        kwargs: dict[str, Any] = {"data": body} if form else {"json": body}
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                headers=headers,
                allow_redirects=False,
                timeout=_TIMEOUT,
                **kwargs,
            ) as resp:
                if resp.status in (301, 302, 303):
                    raise _SessionExpiredError(
                        f"Redirected to {resp.headers.get('Location')}", redirected=True
                    )
                if resp.status >= 400:
                    raise ParkStartConnectionError(f"{path} returned HTTP {resp.status}")
                if "json" not in resp.content_type:
                    raise _SessionExpiredError(
                        f"{path} returned {resp.content_type}", redirected=False
                    )
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ParkStartConnectionError(f"{path} failed: {err}") from err

    async def _request(self, method: str, path: str) -> str:
        """Plain page request (follows redirects)."""
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ParkStartConnectionError(f"{path} failed: {err}") from err


def _raise_unless_successful(result: dict[str, Any], message: str) -> None:
    if result.get("successful"):
        return
    notes = result.get("notifications") or []
    details = "; ".join(str(n.get("message", n)) if isinstance(n, dict) else str(n) for n in notes)
    raise ParkStartError(f"{message}: {details}" if details else message)
