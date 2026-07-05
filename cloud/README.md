# cloud/ — cloud log (lowest-code wins)

Two interchangeable sinks for the TTN uplink stream; both consume the same
webhook, so you can run either or both:

| Option | Code | Infra | Choose when |
|---|---|---|---|
| **A. Google Sheets** (`ttn_webhook_to_sheets.gs`) | ~70 lines Apps Script | none | You just need a shareable log + charts |
| **B. Python listener + SQLite** (`../backend/server.py`) | 1 stdlib file | any always-on machine / free-tier VM | You want the live dashboard + queryable DB for `analysis/` |

Setup for A is in the header comment of the `.gs` file (sheets.new →
Apps Script → deploy web app → TTN custom webhook).

Setup for B: `python3 backend/server.py`, expose it (e.g. `cloudflared
tunnel`, `ngrok`, or a VM with a public IP), then point a TTN custom
webhook at `https://<host>/ttn` with an `X-Webhook-Token` header matching
the `BG_WEBHOOK_TOKEN` environment variable.

**Optional Grafana (zero code):** point Grafana's SQLite data source at
`backend/bananaguard.db`, table `uplinks` — `received_at` is ISO-8601 and
works directly as the time column.
