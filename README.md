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
Every saved plate is a device named after its label in the portal (e.g. "Visitor A"), with a
`binary_sensor.<label>_parked` entity plus session start and end sensors. To start or stop parking a
saved plate, pick its parked entity in the action. Any other plate can be typed as `license_plate`.

### Start a session
```yaml
# A saved plate. The response holds the session id.
action: parkstart.start_session
data:
  entity_id: binary_sensor.visitor_a_parked
  duration: { hours: 2 }   # or end_time: "2026-10-29 18:00:00"
response_variable: parking
```

```yaml
# Any plate, also one that isn't saved.
action: parkstart.start_session
data:
  license_plate: XX-000-X
  duration: { hours: 2 }
```

### Stop a session
```yaml
# A saved plate.
action: parkstart.stop_session
data:
  entity_id: binary_sensor.visitor_a_parked
```

```yaml
# Any plate. Fails when the plate has more than one session.
action: parkstart.stop_session
data:
  license_plate: XX-000-X
```

```yaml
# By the id from a start_session response.
action: parkstart.stop_session
data:
  session_id: "{{ parking.session_id }}"
```

The parked entity also has the plate in its `license_plate` attribute, for use in templates.

### Events
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
