# ha-parkstart

Unofficial Home Assistant integration for the **ParkStart** parking portal (`parkstart-<city>.parkpermit.eu`).
It is meant as a base for automations: actions and read-only entities, no dashboard controls.

> ⚠ Starting a parking session costs money. The integration never retries a start.

## Status
v0.1.0: all of the v1 design is implemented, but it has not been tested against the live portal
inside Home Assistant yet.
See [docs/DESIGN.md](docs/DESIGN.md) for the full v1 design.

Supported municipalities: Gouda.

## Installation (HACS)
1. HACS → ⋮ → Custom repositories → add this repo as an *Integration*.
2. Install **ParkStart** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **ParkStart**.

## Usage
```yaml
# Start a session; the response holds the session id.
action: parkstart.start_session
data:
  license_plate: XX-000-X
  duration: { hours: 2 }   # or end_time: "2026-10-29 18:00:00"
response_variable: parking

# Stop it again (by session_id or license_plate).
action: parkstart.stop_session
data:
  session_id: "{{ parking.session_id }}"
```
Automations can trigger on the `parkstart_session_started` and `parkstart_session_ended` events.
Their `source` is `home_assistant` for sessions changed by these actions, `external` otherwise.

## Repository layout
| Path | Contents |
|------|----------|
| `custom_components/parkstart/` | The integration (bundled async API client in `api.py`) |
| `tests/` | Mocked tests (`pytest-homeassistant-custom-component`); never talk to the live portal |
| `docs/` | `DESIGN.md` (agreed design), `API.md` (reverse-engineered API notes) |
| `tools/` | Bruno/Postman collections and a stdlib reference client used for API discovery |

## Development
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```
