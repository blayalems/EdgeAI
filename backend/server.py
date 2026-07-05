#!/usr/bin/env python3
"""BananaGuard backend — TTN webhook listener + SQLite log + dashboard API.

Zero dependencies (Python 3.9+ stdlib only), one file, one process:

  * POST /ttn          — The Things Stack v3 webhook. Uses decoded_payload
                         when the console formatter ran, otherwise decodes
                         the raw frm_payload itself (decode_payload.py).
                         Optional shared secret: set BG_WEBHOOK_TOKEN and
                         configure the same value as an additional webhook
                         header `X-Webhook-Token` in the TTN console.
  * GET  /api/health   — liveness + row count
  * GET  /api/nodes    — latest uplink per device
  * GET  /api/state?node=ID          — latest uplink + derived fields
  * GET  /api/history?node=ID&n=64   — recent uplinks (oldest first)
  * GET  /api/logs?node=ID&n=50      — uplinks rendered as event entries
  * GET  /…            — static files from the repo root (the dashboard)

Run:
    python3 backend/server.py [--port 8000] [--db backend/bananaguard.db]

Try it without hardware:
    python3 backend/simulate_uplinks.py --once     # or --interval 5
    open http://localhost:8000
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from decode_payload import PayloadError, decode_uplink

if getattr(sys, "frozen", False):
    # PyInstaller bundle: static files are unpacked to _MEIPASS; the DB
    # must live next to the .exe, not in the throwaway unpack dir.
    REPO_ROOT = Path(getattr(sys, "_MEIPASS"))
    DEFAULT_DB = Path(sys.executable).parent / "bananaguard.db"
else:
    REPO_ROOT = Path(__file__).resolve().parents[1]
    DEFAULT_DB = Path(__file__).parent / "bananaguard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS uplinks (
    id             INTEGER PRIMARY KEY,
    received_at    TEXT NOT NULL,
    device_id      TEXT NOT NULL,
    fport          INTEGER,
    fcnt           INTEGER,
    rssi           REAL,
    snr            REAL,
    sf             INTEGER,
    n_pest         INTEGER NOT NULL,
    soil_safe      INTEGER NOT NULL,
    soil_fault     INTEGER NOT NULL,
    camera_fault   INTEGER NOT NULL,
    infer_ready    INTEGER NOT NULL,
    lockout_active INTEGER NOT NULL,
    soil_vwc_pct   INTEGER,
    batt_mv        INTEGER NOT NULL,
    action         TEXT NOT NULL,
    sprays_today   INTEGER NOT NULL,
    raw_hex        TEXT
);
CREATE INDEX IF NOT EXISTS idx_uplinks_dev_time
    ON uplinks (device_id, received_at DESC);
"""

_db_lock = threading.Lock()


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Single Li-ion cell: 3.30 V empty (BG_BATT_CRITICAL_MV) … 4.20 V full.
def batt_pct(mv: int) -> float:
    return round(max(0.0, min(1.0, (mv - 3300) / 900.0)) * 100, 1)


def extract_uplink(msg: dict) -> dict:
    """Pull the fields we store out of a TTS v3 webhook body."""
    up = msg.get("uplink_message") or {}
    dev = (msg.get("end_device_ids") or {}).get("device_id") or "unknown"

    # A formatter-produced decoded_payload is only trusted when complete —
    # a partial one (missing NOT NULL fields) falls back to raw decoding.
    required = ("n_pest", "soil_safe", "soil_fault", "camera_fault",
                "infer_ready", "lockout_active", "batt_mv", "action",
                "sprays_today")
    decoded = up.get("decoded_payload")
    if not decoded or any(decoded.get(k) is None for k in required):
        frm = up.get("frm_payload")
        if not frm:
            raise PayloadError("no complete decoded_payload and no "
                               "frm_payload")
        try:
            raw = base64.b64decode(frm, validate=True)
        except (binascii.Error, ValueError) as e:
            raise PayloadError(f"bad base64 frm_payload: {e}") from e
        decoded = decode_uplink(raw)

    rx = (up.get("rx_metadata") or [{}])[0]
    lora = ((up.get("settings") or {}).get("data_rate") or {}).get("lora") or {}
    raw_hex = None
    if up.get("frm_payload"):
        try:
            raw_hex = base64.b64decode(up["frm_payload"]).hex()
        except (binascii.Error, ValueError):
            pass
    return {
        "received_at": up.get("received_at") or utcnow(),
        "device_id": dev,
        "fport": up.get("f_port"),
        "fcnt": up.get("f_cnt"),
        "rssi": rx.get("rssi"),
        "snr": rx.get("snr"),
        "sf": lora.get("spreading_factor"),
        "raw_hex": raw_hex,
        **{k: decoded.get(k) for k in (
            "n_pest", "soil_safe", "soil_fault", "camera_fault",
            "infer_ready", "lockout_active", "soil_vwc_pct", "batt_mv",
            "action", "sprays_today")},
    }


def insert_uplink(conn: sqlite3.Connection, u: dict) -> int:
    cols = ("received_at device_id fport fcnt rssi snr sf n_pest soil_safe "
            "soil_fault camera_fault infer_ready lockout_active soil_vwc_pct "
            "batt_mv action sprays_today raw_hex").split()
    with _db_lock:
        cur = conn.execute(
            f"INSERT INTO uplinks ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [u.get(c) for c in cols])
        conn.commit()
    return cur.lastrowid


def row_to_state(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["batt_pct"] = batt_pct(d["batt_mv"])
    fault = d["soil_fault"] or d["camera_fault"]
    if fault:
        status = "fault"
    elif d["action"] == "SPRAY":
        status = "spraying"
    elif d["action"] == "LOCKOUT" or d["lockout_active"]:
        status = "blocked"
    elif d["n_pest"] > 5:  # BG_N_EIL
        status = "watch"
    else:
        status = "clear"
    d["status"] = status
    for k in ("soil_safe", "soil_fault", "camera_fault", "infer_ready",
              "lockout_active"):
        d[k] = bool(d[k])
    return d


LOG_STYLES = {
    "SPRAY":   ("spray",   "sprinkler",    "act"),
    "LOCKOUT": ("blocked", "lock_clock",   "warn"),
    "FAULT":   ("fault",   "error",        "warn"),
    "LOG":     ("detect",  "pest_control", "info"),
}


def row_to_log(r: sqlite3.Row) -> dict:
    typ, icon, sev = LOG_STYLES.get(r["action"], LOG_STYLES["LOG"])
    vwc = r["soil_vwc_pct"]
    soil = "fault" if vwc is None else f"{vwc}%"
    if r["action"] == "SPRAY":
        title = "Spray actuated"
    elif r["action"] == "LOCKOUT":
        title = "Spray inhibited — lockout"
    elif r["action"] == "FAULT":
        title = "Sensor fault — spray disabled"
    elif r["n_pest"] > 0:
        title = f"{r['n_pest']} detection(s) in window"
    else:
        title = "Cycle report — no detections"
    return {
        "time": r["received_at"], "node": r["device_id"], "type": typ,
        "icon": icon, "sev": sev, "title": title,
        "detail": (f"N̂_pest {r['n_pest']} · soil {soil} · "
                   f"{r['batt_mv']} mV · sprays today {r['sprays_today']}"),
    }


# Static allowlist: ONLY the dashboard files, never the repo tree (which
# would expose the SQLite DB, firmware sources and keys on a public host).
STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/support.js": "support.js", "/Ring.dc.html": "Ring.dc.html"}


def static_path(route: str):
    """Resolve an allowed static route to a real file path, else None."""
    if route in STATIC_FILES:
        return REPO_ROOT / STATIC_FILES[route]
    if route.startswith("/vendor/"):
        p = (REPO_ROOT / route.lstrip("/")).resolve()
        vendor = (REPO_ROOT / "vendor").resolve()
        if p.is_file() and p.suffix == ".js" and vendor in p.parents:
            return p
    return None


def make_handler(conn: sqlite3.Connection, token: str | None):

    class Handler(SimpleHTTPRequestHandler):

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(REPO_ROOT), **kw)

        def log_message(self, fmt, *args):  # quieter default log
            print(f"[{utcnow()}] {self.address_string()} {fmt % args}")

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---------------- webhook ----------------
        def do_POST(self):
            if urlparse(self.path).path != "/ttn":
                return self._json({"error": "unknown endpoint"}, 404)
            if token and self.headers.get("X-Webhook-Token") != token:
                return self._json({"error": "bad token"}, 403)
            try:
                length = int(self.headers.get("Content-Length", 0))
                msg = json.loads(self.rfile.read(length) or b"{}")
                u = extract_uplink(msg)
                rid = insert_uplink(conn, u)
            except (PayloadError, json.JSONDecodeError, KeyError) as e:
                return self._json({"error": str(e)}, 400)
            self._json({"ok": True, "id": rid, "device_id": u["device_id"],
                        "action": u["action"]})

        # ---------------- API + static ----------------
        def do_GET(self):
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            route = parsed.path

            if route == "/api/health":
                with _db_lock:
                    n = conn.execute("SELECT COUNT(*) c FROM uplinks"
                                     ).fetchone()["c"]
                return self._json({"ok": True, "uplinks": n})

            def limit(key, default, cap):
                try:
                    return max(1, min(int(q.get(key, default)), cap))
                except ValueError:
                    return default

            # "Latest" is by uplink time (then id as tie-break), not by
            # insertion order — a TTN redelivery of an old frame must not
            # regress the dashboard to stale telemetry.
            if route == "/api/nodes":
                with _db_lock:
                    rows = conn.execute(
                        "SELECT u.* FROM uplinks u JOIN (SELECT device_id, "
                        "MAX(received_at || printf('#%012d', id)) mk "
                        "FROM uplinks GROUP BY device_id) t ON "
                        "u.device_id = t.device_id AND "
                        "u.received_at || printf('#%012d', u.id) = t.mk "
                        "ORDER BY u.device_id").fetchall()
                return self._json([row_to_state(r) for r in rows])

            if route == "/api/state":
                node = q.get("node")
                with _db_lock:
                    r = conn.execute(
                        "SELECT * FROM uplinks WHERE (?1 IS NULL OR "
                        "device_id = ?1) ORDER BY received_at DESC, id DESC "
                        "LIMIT 1", (node,)).fetchone()
                if not r:
                    return self._json({"error": "no data yet"}, 404)
                return self._json(row_to_state(r))

            if route == "/api/history":
                node, n = q.get("node"), limit("n", 64, 1000)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM uplinks WHERE "
                        "(?1 IS NULL OR device_id = ?1) ORDER BY "
                        "received_at DESC, id DESC LIMIT ?2) "
                        "ORDER BY received_at ASC, id ASC",
                        (node, n)).fetchall()
                return self._json([row_to_state(r) for r in rows])

            if route == "/api/logs":
                node, n = q.get("node"), limit("n", 50, 500)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM uplinks WHERE (?1 IS NULL OR "
                        "device_id = ?1) ORDER BY received_at DESC, id DESC "
                        "LIMIT ?2", (node, n)).fetchall()
                return self._json([row_to_log(r) for r in rows])

            if route.startswith("/api/"):
                return self._json({"error": "unknown endpoint"}, 404)

            target = static_path(route)
            if target is None:
                return self._json({"error": "not found"}, 404)
            self.path = "/" + str(target.relative_to(REPO_ROOT))
            return super().do_GET()

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    conn = open_db(args.db)
    token = os.environ.get("BG_WEBHOOK_TOKEN")
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port),
                                make_handler(conn, token))
    print(f"BananaGuard backend on http://localhost:{args.port} "
          f"(db: {args.db}, webhook auth: {'ON' if token else 'off'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
