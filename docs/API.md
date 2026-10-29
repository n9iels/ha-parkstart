# ParkStart Gouda – headless API notes (for agents)

Unofficial, reverse-engineered from the web app at https://parkstart-gouda.parkpermit.eu
(ARS T&TT "ParkStart", ASP.NET MVC + Knockout + DevExtreme). There is no public API: the
"API" is the MVC JSON endpoints the page JavaScript calls. Goal of this project: drive it from
Home Assistant so the user never has to open the app.

Discovery tools (in `../tools/`):
- `ParkStart.postman_collection.json` – Postman v2.1 collection (verified with newman 6.2.1 on Auth + Read folders).
- `bruno/` – native Bruno collection (open the folder in Bruno, fill `email`/`password` under
  Collection Settings → Vars; they're empty in the files on purpose). Verified with `@usebruno/cli@4.2.1` on the Auth + Read folders. Prefer this over importing
  the Postman file into Bruno: Bruno only partly translates the `pm.*` scripts, so the token step breaks.
- `parkstart_client.py` – stdlib-only Python reference client + CLI (`PARKSTART_EMAIL` / `PARKSTART_PASSWORD` env vars).

Status (verified 2026-10-29): login, permits, plates, sessions list, **start and stop** all work headlessly.
One live start → list → stop round-trip succeeded (session id 3000001).

## ⚠ Rules for agents
- **Starting a session costs the user real money** (≈ €0.03 for 5 min in the test, even on the
  "unlimited" visitor permit). Never start a session to "test" without explicit user permission, and
  stop any test session right away.
- **Log in once, never in a retry loop.** Failed logins turn on a Google reCAPTCHA (`ShowCaptcha`),
  which would break headless login.
- Credentials: never hard-code or commit them. In HA they belong in the config entry (config flow) or `secrets.yaml`.
  The user's password was pasted into a chat session; suggest they rotate it.

## Auth flow (cookie session)
1. `GET /` → HTML login form. Take the hidden input
   `<input name="__RequestVerificationToken" type="hidden" value="...">`. The response also sets a
   `__RequestVerificationToken` cookie. Both are needed, so keep a cookie jar.
2. `POST /` with `Content-Type: application/x-www-form-urlencoded`:
   ```
   __RequestVerificationToken=<token>&Email=<email>&Password=<password>
   &ReturnUrl=&ShowCaptcha=False&ScreenWidth=1440&ScreenHeight=900
   ```
   - Success: `302 Location: /Customer/PlanSession` + cookie `.AspNet._SSPAuthCookie` (the session).
   - Failure: `200` with the login form again.
   - **Gotcha:** `GET /` while already logged in **logs you out**: `302 → /` with `.AspNet._SSPAuthCookie`
     cleared, then the login form. Follow redirects on step 1 (or start with an empty cookie jar), and don't
     call `GET /` to "check" whether you're logged in.
3. Every later call uses the cookie jar. Cookies seen: `.AspNet._SSPAuthCookie`, `cookiesession1`,
   `SSP_APP_SETTINGS_COOKIE`, `__RequestVerificationToken`.

The session expires after inactivity (the UI warns "U wordt in {0} minuten uitgelogd!"). Detect an expired
session as any AJAX response that redirects (302) to `/` or `/Error/Error`, or returns HTML instead of JSON →
log in again **once**, then retry the call once.

## Required headers for AJAX endpoints
Without these, the server answers `302 → /Error/Error`:
```
X-Requested-With: XMLHttpRequest
Origin: https://parkstart-gouda.parkpermit.eu
Referer: https://parkstart-gouda.parkpermit.eu/Customer/PlanSession   (or /Customer/SessionsOverview/Index/)
```
The AJAX calls need no CSRF token.

## Endpoints

### Permits + start template: `GET /Customer/PlanSession` (HTML)
The view model is embedded in the page as a JS string literal:
`planSession.init('{\"permitList\":[...],\"addItem\":{...}, ...}');`
Extract it with the regex `planSession\.init\('(.*?)'\);` (dotall), then unescape (`\"` → `"`, then `\\` → `\`) and parse as JSON.

- `permitList[]` – this account has one permit:
  `id: 10001`, `permitProduct: "Bezoeker bewoner"`, `paidParkingAreaId: "BEZ_S-1"`,
  `paidParkingAreaIdDescription: "Sector 1 (Example)"`, `unlimitedTimeBalance: true`,
  `timeBalance: 10000`, `status: "Active"`, `sospesPermitPermitId: "1000000001"`.
- `addItem` – the template body for StartParkingSession (includes `startedForUser: 20001`).

### `POST /Customer/PlanSession/GetLicensePlates` (JSON) ✅
Request: `{"permitId": 10001}`
Response:
```json
{"licensePlates":[{"licensePlate":"XX000X","comment":"Visitor A","brand":"VOLKSWAGEN","model":"GOLF",
  "vehicleName":"VOLKSWAGEN GOLF","vehicleColorNl":"ROOD","vehicleColorEn":"Red","vehicleColor":"ROOD","hexCode":"#FF0000"}, ...]}
```
`comment` is the user's label for the plate (e.g. the visitor's name). Good for an HA select.

### `POST /Customer/SessionsOverview/GetParkingSessions/` (form-urlencoded) ✅
Referer: `/Customer/SessionsOverview/Index/`. Body (DevExtreme load options + filter):
```
skip=0&take=50&requireTotalCount=true&cstId=0&vstId=0&searchString=&startIndex=0&pageSize=0
&sortColumn=&sortDirection=&totalRows=0&pageCount=-2147483648
```
Returns **active and planned** sessions only (ended ones disappear):
```json
{"recordsFiltered":1,"recordsTotal":1,"data":[{"id":3000001,"permitId":10001,
  "timeStartUtc":"2026-10-29T16:44:00","timeEndUtc":"2026-10-29T16:49:00",
  "costMoney":0.03,"costTime":5,"lp":"XX000X","lpDescription":"Visitor A",
  "psRightId":"2000000001","name":"Bezoeker bewoner","permitNumber":"1000000001",
  "isActive":true,"permitAreaDescription":"Sector 1 (Example)","zoneCode":"BEZ_S-1",
  "email":"user@example.com","customerPays":true, ...}]}
```
**Gotcha:** `timeStartUtc`/`timeEndUtc` are **Europe/Amsterdam local time, not UTC**. The test session
was sent as `15:44:00Z` and came back as `16:44:00` (CET = UTC+1). Treat them as naive local times.
`isActive: false` presumably means planned (future start), but that is not verified.

### `POST /Customer/PlanSession/StartParkingSession` (JSON) ✅ costs money
Body = the `addItem` template from the PlanSession page with these fields set:
```json
{ ...addItem, "permitId":10001, "currentPermitId":10001, "lp":"XX000X",
  "timeStart":"2026-10-29T15:44:00.000Z", "timeEnd":"2026-10-29T15:49:00.000Z", "saveMode":true }
```
- Times: ISO-8601 **UTC with `Z`** (that is what the JS `Date.toJSON` sends), seconds zeroed.
- `lp`: plate in upper case without dashes. The plate does not have to be saved first, but the UI
  validates it against the RDW (`validateLpOnRdw: true`).
- Response: `{"successful":true}`. The response does **not** include the session id. Fetch it from
  GetParkingSessions (match on `lp`).
- On failure: `{"successful":false, "notifications":[...]}` with user-facing messages (not observed yet).
- A future `timeStart` probably creates a planned session (the UI allows it). Not verified.

### `POST /Customer/PlanSession/StopParkingSession` (JSON) ✅
Body: `{"parkingSessionId": 3000001}` → `{"successful":true}`. The session then disappears from the list.
The UI uses the same call for "cancel planned session".

### Seen in JS, not tested
All are under `/Customer/PlanSession/` with the same headers:
- `GetVehicleInfo` (GET `?permitId=&licensePlate=`) → `{lpSelect:{vehicleName,vehicleColor,hexCode}}` (RDW lookup)
- `CalculateBalance`, `GetBalanceInfo`, `GetFlatBalance` – body = addItem; cost/balance preview before starting
- `StopEditedParkingSession` + `StartEditedParkingSession` – editing a session's end time = stop then start, body = addItem
- `GenerateTimelineData`, `GetGarages`, `GetBalanceInfoForGarage` – UI timeline/garages, not relevant
- `/Customer/VisitorDiscountCode/CreateDiscountCode/` – "visitor pays" flow

JS sources if you need more (served under `/scripts/components/`):
`views/customer/planSession/planSession.js`, `views/common/planSession/planSessionBase.js`,
`views/customer/stopSession/stopSessionDialog.js`, `views/customer/sessionsOverview/sessionsOverview.js`,
`views/common/viewBase.js` (ajax helpers).

## Home Assistant integration
The agreed design is in `DESIGN.md` (next to this file). Integration-specific notes:
- Use HA's `async_create_clientsession` with its own cookie jar, **not** the shared session, so cookies don't
  leak between integrations.
- Other municipalities probably run the same software under `parkstart-<city>.parkpermit.eu` (unverified).

## Quick test
```bash
export PARKSTART_EMAIL=... PARKSTART_PASSWORD=...
python3 tools/parkstart_client.py sessions          # read-only
npx newman run tools/ParkStart.postman_collection.json --folder Auth --folder Read \
  --env-var email=$PARKSTART_EMAIL --env-var password=$PARKSTART_PASSWORD
```
