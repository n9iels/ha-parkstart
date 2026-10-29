# ha-parkstart – design (v1)

Home Assistant integration for the ParkStart parking portal (reverse-engineered API, see `API.md`).
It is meant as a **base for automations**: actions and read-only entities first, no dashboard controls.

Agreed 2026-10-29.

## Scope of v1
- Start and stop parking sessions.
- Show active parking sessions.
- Mirror ("import") the saved license plates as devices.

Out of scope for v1: planned (future) sessions, dashboard entities (buttons/selects/numbers), a calendar
entity, editing plates, permit balance sensors.

## Repo & packaging
- This folder becomes the git repo `ha-parkstart`, laid out for HACS (`hacs.json`, `manifest.json`).
- Integration code lives in `custom_components/parkstart/`.
- Reverse-engineering material moves out of the root: `API.md` → `docs/`, Postman/Bruno/`parkstart_client.py` → `tools/`.
  Check the Bruno and Postman files for credentials before the first commit. `.gitignore` covers
  `__pycache__/` and `.DS_Store`.
- The API client is bundled as `custom_components/parkstart/api.py` (no PyPI package). It uses async
  `aiohttp` with its **own** client session and cookie jar (`async_create_clientsession`), not HA's shared one.
- Translations: English and Dutch (`translations/en.json`, `translations/nl.json`). Code and docs in English.

## Configuration
- **Config flow**: email, password and a municipality dropdown that fills in the base URL.
  The dropdown is a fixed list (only Gouda → `https://parkstart-gouda.parkpermit.eu` for now) with no
  custom-URL option. New cities are added in code after they are checked.
  The credentials are checked with exactly one login attempt.
- **Options flow**: sessions poll interval (default 10 min) and max session duration (default 24 h).
- One config entry = one ParkStart account.

## Auth
- An expired session (302 to `/` or `/Error/Error`, or HTML instead of JSON) leads to **one** re-login and
  **one** retry of the call.
- A failed login raises `ConfigEntryAuthFailed`, which starts HA's re-auth flow and stops polling.
- Logins are never retried automatically, because failed logins enable a reCAPTCHA.
- `StartParkingSession` costs money. Like every action, it logs in again once after an expired-session
  **redirect** (the session timeout is unknown, and a redirect means the start was not processed).
  It is never retried after a network error or an unexpected HTML answer, because the start may have
  gone through. (Changed 2026-10-29.)

## Devices & entities (all read-only)
**Permit device** (one per permit; the code handles N permits, the account has one today)
- `sensor.<permit>_active_sessions`: the number of active sessions, with the full session list as an attribute.
  Sessions for plates that are not saved in the portal appear only here.

**Plate devices** (one per saved plate, linked to the permit with `via_device`)
- A read-only mirror of the portal's saved plates, named after the plate's `comment` (falling back to the plate).
  Devices and entities are added and removed as the portal list changes. HA never edits plates.
- `binary_sensor.<plate>_parked`: on while the plate has an active session.
  Attributes: `license_plate`, `session_id`, `cost` (€), `zone`.
- `sensor.<plate>_session_start` and `sensor.<plate>_session_end`: timestamps, **unknown** when there is no
  session. ("Unavailable" is kept for "the integration is broken".)
- If a plate has more than one session, the entities show the active one, otherwise the earliest one.
- Device identifier: `(permit_id, plate)`.

No buttons, selects or numbers in v1. They may be added later.

## Updates
- `DataUpdateCoordinator`: sessions every 10 min (configurable), plates every 60 min.
- Both refresh right after any `start_session` / `stop_session` action.
- A local timer, scheduled from each session's end time, switches off `parked`, clears the session sensors and
  fires `parkstart_session_ended` at the end time without waiting for the next poll.
  Sessions started outside HA show up on the next poll.
- Times: the API returns `timeStartUtc`/`timeEndUtc` as **naive Europe/Amsterdam local times**, which are converted
  with `zoneinfo("Europe/Amsterdam")`. Times sent to the API are UTC ISO-8601 with `Z`, seconds zeroed.

## Actions
### `parkstart.start_session`
- `license_plate` (required): any plate, not only saved ones. It is normalised to upper case without dashes.
  The selector is free text: `services.yaml` selectors can't list the saved plates dynamically.
- Exactly one of `duration` (HA duration selector) or `end_time` (datetime). Neither or both → error.
- Rejected before calling the API if the duration is longer than the configured max.
- `permit` (device selector): only needed when the account has more than one permit.
- Starts **now** only. There is no `start_time` (the planned-session behavior is unverified and costs money to verify).
- Returns `session_id` as response data. The start call does not return it, so it is found by re-fetching
  sessions and matching on the plate.

### `parkstart.stop_session`
- One of `session_id` or `license_plate`.
- If the plate has more than one session → error. The integration does not guess.

## Events
`parkstart_session_started` and `parkstart_session_ended`, both with:
`session_id`, `license_plate`, `label` (plate comment or null), `permit_id`, `start`, `end`, `cost`, `source`.

- `source`: `"home_assistant"` when the change came from a `parkstart.*` action, `"external"` when it was spotted
  by polling (started in the app or website) or the session reached its end time.
- Both events are found by diffing the session list between polls. `ended` also fires from the local
  end-time timer or when a session drops off the list.

## Tests
- `pytest-homeassistant-custom-component` with anonymised recorded JSON/HTML fixtures and a mocked HTTP layer.
- **No live session start in any test.** A live read-only smoke test (login, plates, sessions) is only ever
  run by hand.

## Decision log
| # | Decision | Why |
|---|----------|-----|
| 1 | HACS repo, client bundled | Simple to install; PyPI only matters for HA core |
| 2 | Entry = account, device = permit, plates under permit | Plates and starts are scoped per permit |
| 3 | Plates are a read-only mirror, one device per plate | Lets automations target a single car; the portal stays the source of truth |
| 4 | Actions first, with response data | The integration is a base for automations |
| 5 | Active-sessions sensor + started/ended events | Sessions come and go; events also catch sessions started outside HA |
| 6 | No planned sessions in v1 | Unverified API behavior, and verifying it costs money |
| 7 | Single re-login, never retry login; start retried only after an expired-session redirect | reCAPTCHA risk; starting costs money, but a redirect means nothing was started |
| 8 | 10 min sessions / 60 min plates, refresh after actions | Few logins; the local end timer covers the lag at session end |
| 9 | Explicit duration required, max-duration cap | Prevents accidental expensive sessions |
| 10 | Fixed municipality dropdown | Only Gouda has been checked |
| 11 | Mocked tests only | Live starts cost money |
| 12 | Local end-time timer | Keeps "parked" accurate despite 10 min polling |
| 14 | Any plate can be started | One-off visitors; matches the portal |
| 15 | No dashboard entities in v1 | Keep v1 to actions and read-only entities; may come later |
| 20 | `source` field on events | Automations can tell their own sessions apart from ones started in the app |
