# backend/ — TTN listener, SQLite log, dashboard API

One stdlib-only Python file (`server.py`): receives The Things Stack v3
webhooks on `POST /ttn`, stores every uplink in SQLite, serves a JSON API
and the dashboard itself. No pip installs, no framework.

```sh
python3 backend/server.py                 # http://127.0.0.1:8000
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
| `GET /api/export.csv?node=ID&n=1000` | oldest-first raw telemetry export for bench/field validation (maximum 10,000 rows) |
| anything else | dashboard static files only (allowlist: `index.html`, `support.js`, `Ring.dc.html`, `vendor/*.js`) — the DB, firmware and other repo files are never reachable over HTTP |

Derived `status` mirrors the dashboard vocabulary: `fault` > `spraying` >
`blocked` > `watch` (N̂_pest > EIL) > `clear`. State rows also include
`online`, `age_seconds`, `safety_violation`, `eil_threshold`, and
`window_minutes`. A reported spray that conflicts with any soil, camera,
inference, or lockout flag is promoted to a safety fault instead of being
shown as a successful actuation.

TTS webhook retries are idempotent when the message contains a session key
and frame counter (or a source timestamp and frame counter). The response
includes `"duplicate": true` for an already-stored frame, preventing retry
traffic from inflating research observations.

## Webhook auth

Set `BG_WEBHOOK_TOKEN` and add the same value as an `X-Webhook-Token`
additional header in the TTN webhook config. The server listens on localhost
by default. Binding to a LAN/public interface is refused unless the token is
set:

```sh
BG_WEBHOOK_TOKEN="replace-with-a-long-random-value" \
  python3 backend/server.py --host 0.0.0.0
```

Webhook bodies are capped at 64 KiB and both raw and formatter-decoded
payloads are type/range checked. When both representations are present they
must agree before the row is stored.

## Tests

```sh
python3 backend/test_backend.py    # 22 tests: webhook, auth, API, export, safety
```
