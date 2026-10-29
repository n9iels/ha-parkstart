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
Each permit is a device ("ParkStart Sector 1"). Every saved plate is one entity on it, named after its
label in the portal, e.g. `binary_sensor.parkstart_sector_1_visitor_a`. It is `on` while the plate is
parked, with the attributes `license_plate`, `session_id`, `session_start`, `session_end`, `cost` and `zone`.

The actions work like other Home Assistant actions: pick the plate's entity as the **target**, one plate
per call. A plate that isn't saved can be typed as `license_plate`.

### Start a session
```yaml
# A saved plate. The response holds the session id.
action: parkstart.start_session
target:
  entity_id: binary_sensor.parkstart_sector_1_visitor_a
data:
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
target:
  entity_id: binary_sensor.parkstart_sector_1_visitor_a
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

`permit` is only needed for a typed `license_plate` when the account has more than one permit.
In templates, e.g. `{{ state_attr('binary_sensor.parkstart_sector_1_visitor_a', 'session_end') }}`.

### Automations: "when a parking session starts"
In the automation editor: **Add trigger → Device → ParkStart Sector 1**, then pick
**Parking session started** or **Parking session ended**. This covers every session on the permit,
also for plates that aren't saved. Optionally limit it to sessions started or stopped by Home Assistant
(`home_assistant`) or in the app/website (`external`).

```yaml
triggers:
  - trigger: device
    domain: parkstart
    device_id: <ParkStart Sector 1 device>
    type: session_started
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Parking started for {{ trigger.event.data.label or trigger.event.data.license_plate }}"
```

The session details are in `trigger.event.data`: `session_id`, `license_plate`, `label`, `permit_id`,
`start`, `end`, `cost` and `source`.

When it fires: right away for sessions started by the actions, at the next poll (up to 10 minutes by
default) for sessions started in the app or website. "Ended" fires at the end time itself. Sessions that
already exist when Home Assistant starts don't fire "started".

For one saved plate, a state trigger on its entity (`off` → `on`) works too. In YAML you can also use the
underlying events `parkstart_session_started` / `parkstart_session_ended` directly.

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
