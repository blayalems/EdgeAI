# cloud/ — cloud log (lowest-code wins)

Two alternative sinks can consume the TTN uplink stream, but they are **not
interchangeable evidence stores**:

| Option | Code | Infra | Choose when |
|---|---|---|---|
| **A. Legacy Google Sheets helper** (`ttn_webhook_to_sheets.gs`) | ~70 lines Apps Script | none | You only need a non-evidentiary convenience log + charts |
| **B. Python listener + SQLite** (`../backend/server.py`) | 1 stdlib file | any always-on machine / free-tier VM | You want the local Actual dashboard + queryable DB for `analysis/` |

Setup for A is in the header comment of the `.gs` file. It is retained for
low-code demonstrations, but its optional query-string secret, timestamp
fallback, lack of durable retry de-duplication, and weaker validation mean its
rows are **not** `source_kind=field`, cannot populate Actual mode, and must not
be used as research/field evidence. Use the Python backend for that contract.

Setup for B: set a long random `BG_WEBHOOK_TOKEN`, run
`python3 backend/server.py` on its default loopback address, and terminate
HTTPS in a hardened reverse proxy. Expose **only** authenticated `POST /ttn`
to The Things Stack and forward it to the loopback listener; do not publish or
tunnel the complete built-in server. Configure the TTN custom webhook with an
`X-Webhook-Token` header matching the environment variable. A remote dashboard
viewer needs a separately reviewed authentication, TLS, and origin policy;
that public deployment surface is intentionally not implemented here.

**Optional Grafana (zero code):** point Grafana's SQLite data source at
`backend/bananaguard.db`, table `uplinks` — `received_at` is ISO-8601 and
works directly as the time column.
