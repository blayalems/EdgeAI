# backend/ — TTN listener, SQLite log, dashboard API

One stdlib-only Python server (`server.py`): receives authenticated The Things
Stack v3 field webhooks on `POST /ttn`, stores provenance with every uplink,
serves a read-only JSON API, and serves the dashboard itself. No pip installs,
no framework. Actual data defaults to `source=field`; simulator and historical
unclassified rows can never silently satisfy an Actual request.

```sh
BG_WEBHOOK_TOKEN="replace-with-a-long-random-value" \
  python3 backend/server.py                       # authenticated field ingest
open http://localhost:8000                        # choose Actual in the UI

python3 backend/server.py --allow-simulator       # local provenance exercise
python3 backend/simulate_uplinks.py --once        # fake rows -> /demo/ttn
curl 'http://localhost:8000/api/nodes?source=simulator'
```

The dashboard's visual Demo is generated in-page and intentionally performs
no API requests. Simulator rows exercise the backend boundary only; they are
never promoted into the Actual UI.

## Endpoints

| Route | Purpose |
|---|---|
| `POST /ttn` | field-only TTS v3 webhook (uses `decoded_payload`, falls back to raw `frm_payload`) |
| `POST /demo/ttn` | simulator-only ingest; disabled unless `--allow-simulator`, and always restricted to loopback clients |
| `GET /api/meta` | API version, server time, backend profile, field-ingest/read-only/field-ready flags, stale threshold, capabilities, and per-source counts |
| `GET /api/health` | liveness, total uplinks, and per-source counts |
| `GET /api/nodes[?source=field\|simulator]` | latest uplink per enabled device (+ registry metadata and derived state) |
| `GET /api/state?node=ID[&source=…]` | latest state for one node |
| `GET /api/history?node=ID&n=64[&source=…]` | recent uplinks, oldest first (sparklines) |
| `GET /api/logs?node=ID&n=50[&source=…]` | uplinks rendered as event-log entries |
| `GET /api/export.csv?node=ID&n=1000[&source=…]` | oldest-first raw telemetry export (maximum 10,000 rows) |
| anything else | dashboard static files only (allowlist: `index.html`, `support.js`, `Ring.dc.html`, exact license files, `vendor/*.js`, and the exact offline-font files) — the DB, firmware and other repo files are never reachable over HTTP |

The data endpoints accept only `field` or `simulator`, and default to `field`.
Migrated `legacy_unknown` rows remain in SQLite for preservation/audit but are
not queryable as Actual telemetry. JSON and CSV rows include `source_kind`,
`received_at`/`source_time`, `ingested_at`, `payload_version`, and
`application_id`. Source and ingestion ages are calculated by the server.
Field source timestamps are required and must be offset-bearing RFC3339; they
cannot silently fall back to ingestion time. Source timestamps more than five
minutes in the future are rejected.

Derived `status` mirrors the dashboard vocabulary: `fault` > `held` (pest
pressure over EIL while the soil gate is unsafe) > `reported` (a completed
spray report) > `blocked` > `watch` > `clear`. A `SPRAY` uplink never means the
actuator is active at API request time. State rows include `action_status`,
`treatment_held`, `online`, source/ingestion ages, `safety_violation`,
`eil_threshold`, and `window_minutes`. A reported spray that conflicts with
any safety flag is promoted to a safety fault.

Field ingest requires a valid TTS application ID, frame counter, and
offset-bearing source timestamp. TTS webhook retries are idempotent within each
provenance source and application, using the session key and frame counter (or
the required source timestamp and frame counter). The response includes
`"duplicate": true` for an already-stored frame, preventing retry traffic from
inflating research observations. The opt-in simulator remains lenient and its
rows cannot enter Actual mode.

## Database migrations and node registry

Startup applies numbered, forward-only migrations and records them in
`schema_migrations` (`PRAGMA user_version` is also maintained). Existing rows
are preserved and labelled `legacy_unknown`; no historical source is guessed.

Node metadata is managed locally, never over an unauthenticated HTTP write
route. Unregistered nodes fall back to their raw `device_id` and no invented
location. A disabled registry row is omitted from data APIs.

```sh
python3 backend/manage_nodes.py list
python3 backend/manage_nodes.py set bg-n01 --name "North Plot" --block B1 \
  --latitude 1.3521 --longitude 103.8198
python3 backend/manage_nodes.py disable bg-n01
python3 backend/manage_nodes.py enable bg-n01
```

## Webhook auth

Set `BG_WEBHOOK_TOKEN` to a cryptographically random value of at least 32 bytes
and add the same value as an `X-Webhook-Token` additional header in the TTN
webhook config. Weak tokens are rejected at startup. Without a configured token,
`POST /ttn` returns `503` and stores nothing. The built-in server is
localhost-only; it deliberately refuses LAN/public bindings because the
viewer API does not yet implement authentication or TLS:

```sh
BG_WEBHOOK_TOKEN="replace-with-a-long-random-value" \
  python3 backend/server.py
```

Webhook bodies are capped at 64 KiB and both raw and formatter-decoded
payloads are type/range checked. When both representations are present they
must agree before the row is stored.

To expose a deployment later, terminate authenticated HTTPS in a hardened
reverse proxy and forward only to the loopback listener after its viewer-auth
policy has been reviewed. The API intentionally sends no wildcard CORS header.

`POST /demo/ttn` does not reuse `/ttn`: it is a distinct provenance boundary,
is disabled by default, and rejects non-loopback peers. Start with
`--allow-simulator` before running
`simulate_uplinks.py`. The API stays read-only in both profiles; there are no
actuator/configuration command endpoints.

`/api/meta` reports whether authenticated field ingest is configured and keeps
`field_ready: false` by default. `--field-ready` is ignored unless a valid
webhook token is configured; it is an explicit operator attestation and should only be
used after the physical model, RF, calibration, persisted safety evidence, and
bench/field gates have actually passed.

## Tests

```sh
python3 backend/test_backend.py    # 39 tests: migration, provenance, auth, API, export, safety
```
