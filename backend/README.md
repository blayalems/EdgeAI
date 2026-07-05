# backend/ — TTN listener, SQLite log, dashboard API

One stdlib-only Python file (`server.py`): receives The Things Stack v3
webhooks on `POST /ttn`, stores every uplink in SQLite, serves a JSON API
and the dashboard itself. No pip installs, no framework.

```sh
python3 backend/server.py                 # http://localhost:8000
python3 backend/simulate_uplinks.py --interval 5   # fake nodes, no hardware
open http://localhost:8000                # dashboard, now on live data
```

## Endpoints

| Route | Purpose |
|---|---|
| `POST /ttn` | TTS v3 webhook (uses `decoded_payload`, falls back to decoding raw `frm_payload`) |
| `GET /api/health` | liveness + uplink count |
| `GET /api/nodes` | latest uplink per device (+ derived `batt_pct`, `status`) |
| `GET /api/state?node=ID` | latest state for one node |
| `GET /api/history?node=ID&n=64` | recent uplinks, oldest first (sparklines) |
| `GET /api/logs?node=ID&n=50` | uplinks rendered as event-log entries |
| anything else | static files from the repo root (the dashboard) |

Derived `status` mirrors the dashboard vocabulary: `fault` > `spraying` >
`blocked` > `watch` (N̂_pest > EIL) > `clear`.

## Webhook auth

`export BG_WEBHOOK_TOKEN=...` and add the same value as an
`X-Webhook-Token` additional header in the TTN webhook config. Unset = no
auth (fine on localhost, not on a public host).

## Tests

```sh
python3 backend/test_backend.py    # 10 tests: webhook paths, auth, API, decode
```
