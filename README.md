# ha-parkstart

Unofficial Home Assistant integration for the **ParkStart** parking portal (`parkstart-<city>.parkpermit.eu`).
It is meant as a base for automations: actions and read-only entities, no dashboard controls.

> ⚠ Starting a parking session costs money. The integration never retries a start.

## Status
Work in progress (v0.1.0). Done: API client, config flow (with re-auth) and options flow.
Next: coordinator, entities, `parkstart.start_session` / `parkstart.stop_session` actions and events.
See [docs/DESIGN.md](docs/DESIGN.md) for the full v1 design.

Supported municipalities: Gouda.

## Installation (HACS)
1. HACS → ⋮ → Custom repositories → add this repo as an *Integration*.
2. Install **ParkStart** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **ParkStart**.

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
