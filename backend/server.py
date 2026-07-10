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
  * GET  /api/export.csv?node=ID     — validation-ready telemetry export
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
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from decode_payload import PayloadError, decode_uplink

MAX_WEBHOOK_BYTES = 64 * 1024
N_EIL = 5
WINDOW_MINUTES = 30
STALE_AFTER_SECONDS = 90 * 60
DEVICE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,34}[a-z0-9])?$")

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
    raw_hex        TEXT,
    event_key      TEXT
);
CREATE INDEX IF NOT EXISTS idx_uplinks_dev_time
    ON uplinks (device_id, received_at DESC);
"""

_db_lock = threading.Lock()


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Forward-only, data-preserving migration for databases created before
    # webhook retry de-duplication was added.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(uplinks)")}
    if "event_key" not in columns:
        conn.execute("ALTER TABLE uplinks ADD COLUMN event_key TEXT")
    # SQLite unique indexes allow multiple NULLs, so simulator uplinks that
    # intentionally have no stable event key remain insertable.
    conn.execute("DROP INDEX IF EXISTS idx_uplinks_event_key")
    conn.execute("CREATE UNIQUE INDEX idx_uplinks_event_key "
                 "ON uplinks (event_key)")
    conn.commit()
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def norm_time(ts) -> str:
    """Normalize any RFC3339 timestamp to UTC with fixed microsecond
    precision so lexicographic order in SQLite equals chronological
    order ('...00Z' would otherwise sort AFTER '...00.500Z'). Missing source
    timestamps use receipt time; malformed supplied timestamps are rejected
    so validation evidence is never silently re-dated."""
    if ts is None or ts == "":
        return utcnow()
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise PayloadError("received_at must be an RFC3339 timestamp")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _int_field(obj: dict, key: str, low: int, high: int,
               *, optional: bool = False) -> int | None:
    value = obj.get(key)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"{key} must be an integer")
    if not low <= value <= high:
        raise PayloadError(f"{key} outside {low}..{high}")
    return value


def _bool_field(obj: dict, key: str) -> bool:
    value = obj.get(key)
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise PayloadError(f"{key} must be boolean")


def _number_field(obj: dict, key: str, low: float, high: float):
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"{key} must be numeric")
    if not math.isfinite(value) or not low <= value <= high:
        raise PayloadError(f"{key} outside {low}..{high}")
    return value


def normalize_decoded(decoded: dict) -> dict:
    """Validate the formatter output before it reaches SQLite.

    SQLite's dynamic types would otherwise accept values such as
    ``n_pest='many'`` and the later comparison in ``row_to_state`` would
    crash every request for that node.
    """
    if not isinstance(decoded, dict):
        raise PayloadError("decoded_payload must be an object")
    if decoded.get("version", 1) != 1:
        raise PayloadError(f"unknown payload version {decoded.get('version')!r}")
    action = decoded.get("action")
    if action not in ("LOG", "SPRAY", "LOCKOUT", "FAULT"):
        raise PayloadError(f"unknown action {action!r}")
    vwc = decoded.get("soil_vwc_pct")
    if vwc is not None:
        vwc = _int_field(decoded, "soil_vwc_pct", 0, 100)
    return {
        "n_pest": _int_field(decoded, "n_pest", 0, 0xFFFF),
        "soil_safe": _bool_field(decoded, "soil_safe"),
        "soil_fault": _bool_field(decoded, "soil_fault"),
        "camera_fault": _bool_field(decoded, "camera_fault"),
        "infer_ready": _bool_field(decoded, "infer_ready"),
        "lockout_active": _bool_field(decoded, "lockout_active"),
        "soil_vwc_pct": vwc,
        "batt_mv": _int_field(decoded, "batt_mv", 0, 0xFFFF),
        "action": action,
        "sprays_today": _int_field(decoded, "sprays_today", 0, 0xFF),
    }


def _event_key(device_id: str, up: dict, received_at: str) -> str | None:
    """Return a stable, opaque key for a real TTN frame.

    TTS may retry a webhook delivery. Counting the retry as a new sample
    corrupts validation logs, so session+frame-counter is preferred. A
    timestamp+counter key is the safe fallback. Simulator messages that omit
    both are intentionally not de-duplicated.
    """
    fcnt = up.get("f_cnt")
    session = up.get("session_key_id")
    source_time = up.get("received_at")
    if fcnt is None:
        return None
    if session:
        source = f"session:{device_id}:{session}:{fcnt}"
    elif source_time:
        source = f"time:{device_id}:{received_at}:{fcnt}"
    else:
        return None
    return hashlib.sha256(source.encode()).hexdigest()


# Single Li-ion cell: 3.30 V empty (BG_BATT_CRITICAL_MV) … 4.20 V full.
def batt_pct(mv: int) -> float:
    return round(max(0.0, min(1.0, (mv - 3300) / 900.0)) * 100, 1)


def extract_uplink(msg: dict) -> dict:
    """Pull the fields we store out of a TTS v3 webhook body."""
    if not isinstance(msg, dict):
        raise PayloadError("webhook body must be an object")
    up = msg.get("uplink_message") or {}
    if not isinstance(up, dict):
        raise PayloadError("uplink_message must be an object")
    ids = msg.get("end_device_ids") or {}
    if not isinstance(ids, dict):
        raise PayloadError("end_device_ids must be an object")
    dev = ids.get("device_id")
    if not isinstance(dev, str) or not DEVICE_ID_RE.fullmatch(dev):
        raise PayloadError("invalid or missing device_id")

    # A formatter-produced decoded_payload is only trusted when complete —
    # a partial one (missing NOT NULL fields) falls back to raw decoding.
    required = ("n_pest", "soil_safe", "soil_fault", "camera_fault",
                "infer_ready", "lockout_active", "batt_mv", "action",
                "sprays_today")
    decoded = up.get("decoded_payload")
    decoded_complete = (isinstance(decoded, dict) and decoded and
                        not any(decoded.get(k) is None for k in required))
    raw_decoded = None
    raw = None
    frm = up.get("frm_payload")
    if frm:
        if not isinstance(frm, str):
            raise PayloadError("frm_payload must be base64 text")
        frm = up.get("frm_payload")
        try:
            raw = base64.b64decode(frm, validate=True)
        except (binascii.Error, ValueError) as e:
            raise PayloadError(f"bad base64 frm_payload: {e}") from e
        raw_decoded = normalize_decoded(decode_uplink(raw))
    if decoded_complete:
        decoded = normalize_decoded(decoded)
        if raw_decoded and decoded != raw_decoded:
            raise PayloadError("decoded_payload does not match frm_payload")
    elif raw_decoded:
        decoded = raw_decoded
    else:
        raise PayloadError("no complete decoded_payload and no frm_payload")

    metadata = up.get("rx_metadata") or []
    if not isinstance(metadata, list):
        raise PayloadError("rx_metadata must be an array")
    rx = metadata[0] if metadata else {}
    if not isinstance(rx, dict):
        raise PayloadError("rx_metadata entries must be objects")
    settings = up.get("settings") or {}
    if not isinstance(settings, dict):
        raise PayloadError("settings must be an object")
    data_rate = settings.get("data_rate") or {}
    if not isinstance(data_rate, dict):
        raise PayloadError("data_rate must be an object")
    lora = data_rate.get("lora") or {}
    if not isinstance(lora, dict):
        raise PayloadError("LoRa data_rate must be an object")
    raw_hex = None
    if raw is not None:
        raw_hex = raw.hex()
    received_at = norm_time(up.get("received_at"))
    return {
        "received_at": received_at,
        "device_id": dev,
        "fport": _int_field(up, "f_port", 0, 255, optional=True),
        "fcnt": _int_field(up, "f_cnt", 0, 0xFFFFFFFF, optional=True),
        "rssi": _number_field(rx, "rssi", -250, 50),
        "snr": _number_field(rx, "snr", -100, 100),
        "sf": _int_field(lora, "spreading_factor", 5, 12,
                         optional=True),
        "raw_hex": raw_hex,
        "event_key": _event_key(dev, up, received_at),
        **{k: decoded.get(k) for k in (
            "n_pest", "soil_safe", "soil_fault", "camera_fault",
            "infer_ready", "lockout_active", "soil_vwc_pct", "batt_mv",
            "action", "sprays_today")},
    }


def insert_uplink(conn: sqlite3.Connection, u: dict) -> tuple[int, bool]:
    cols = ("received_at device_id fport fcnt rssi snr sf n_pest soil_safe "
            "soil_fault camera_fault infer_ready lockout_active soil_vwc_pct "
            "batt_mv action sprays_today raw_hex event_key").split()
    with _db_lock:
        cur = conn.execute(
            f"INSERT INTO uplinks ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))}) "
            "ON CONFLICT(event_key) DO NOTHING",
            [u.get(c) for c in cols])
        conn.commit()
        if cur.rowcount:
            return cur.lastrowid, True
        row = conn.execute("SELECT * FROM uplinks WHERE event_key = ?",
                           (u["event_key"],)).fetchone()
        evidence_fields = ("device_id", "fcnt", "n_pest", "soil_safe",
                           "soil_fault", "camera_fault", "infer_ready",
                           "lockout_active", "soil_vwc_pct", "batt_mv",
                           "action", "sprays_today", "raw_hex")
        if any(row[k] != u.get(k) for k in evidence_fields):
            raise PayloadError("duplicate frame key conflicts with stored payload")
    return row["id"], False


def safety_violation(r) -> bool:
    return bool(r["action"] == "SPRAY" and
                (not r["soil_safe"] or r["soil_fault"] or
                 r["camera_fault"] or not r["infer_ready"] or
                 r["lockout_active"]))


def row_to_state(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("event_key", None)
    d["batt_pct"] = batt_pct(d["batt_mv"])
    violation = safety_violation(r)
    fault = (d["soil_fault"] or d["camera_fault"] or
             not d["infer_ready"] or d["action"] == "FAULT" or violation)
    if fault:
        status = "fault"
    elif d["action"] == "SPRAY":
        status = "spraying"
    elif d["action"] == "LOCKOUT" or d["lockout_active"]:
        status = "blocked"
    elif d["n_pest"] > N_EIL:
        status = "watch"
    else:
        status = "clear"
    d["status"] = status
    d["safety_violation"] = violation
    d["eil_threshold"] = N_EIL
    d["window_minutes"] = WINDOW_MINUTES
    try:
        observed = datetime.fromisoformat(d["received_at"])
        age = max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))
    except (TypeError, ValueError):
        age = STALE_AFTER_SECONDS + 1
    d["age_seconds"] = age
    d["online"] = age <= STALE_AFTER_SECONDS
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
    violation = safety_violation(r)
    reported_fault = bool(r["soil_fault"] or r["camera_fault"] or
                          not r["infer_ready"] or r["action"] == "FAULT")
    typ, icon, sev = (("fault", "report", "warn") if violation else
                      ("fault", "error", "warn") if reported_fault else
                      LOG_STYLES.get(r["action"], LOG_STYLES["LOG"]))
    vwc = r["soil_vwc_pct"]
    soil = "fault" if vwc is None else f"{vwc}%"
    if violation:
        title = "Safety invariant violated — inspect node"
    elif r["action"] == "SPRAY":
        title = "Spray actuated"
    elif r["action"] == "LOCKOUT":
        title = "Spray inhibited — lockout"
    elif reported_fault:
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


def static_route(route: str):
    """Return the rewritten URL path for an allowed static route, else
    None. Works on URL paths only — never reconstructs a path from a
    resolved filesystem path, because on Windows resolve() can expand
    8.3 short names (e.g. inside the PyInstaller temp dir) into a form
    that no longer matches REPO_ROOT."""
    if route in STATIC_FILES:
        return "/" + STATIC_FILES[route]
    if route.startswith("/vendor/"):
        p = (REPO_ROOT / route.lstrip("/")).resolve()
        vendor = (REPO_ROOT / "vendor").resolve()
        if p.is_file() and p.suffix == ".js" and vendor in p.parents:
            return route
    return None


def make_handler(conn: sqlite3.Connection, token: str | None):

    class Handler(SimpleHTTPRequestHandler):

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(REPO_ROOT), **kw)

        def log_message(self, fmt, *args):  # quieter default log
            print(f"[{utcnow()}] {self.address_string()} {fmt % args}")

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---------------- webhook ----------------
        def do_POST(self):
            if urlparse(self.path).path != "/ttn":
                return self._json({"error": "unknown endpoint"}, 404)
            supplied = self.headers.get("X-Webhook-Token", "")
            if token and not hmac.compare_digest(supplied, token):
                return self._json({"error": "bad token"}, 403)
            try:
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    return self._json({"error": "invalid Content-Length"}, 400)
                if length <= 0:
                    return self._json({"error": "empty webhook body"}, 400)
                if length > MAX_WEBHOOK_BYTES:
                    return self._json({"error": "webhook body too large"}, 413)
                msg = json.loads(self.rfile.read(length) or b"{}")
                u = extract_uplink(msg)
                rid, inserted = insert_uplink(conn, u)
            except (PayloadError, json.JSONDecodeError, KeyError,
                    TypeError, OverflowError) as e:
                return self._json({"error": str(e)}, 400)
            self._json({"ok": True, "id": rid, "device_id": u["device_id"],
                        "action": u["action"], "duplicate": not inserted})

        def do_HEAD(self):
            """Apply the same static allowlist to HEAD as GET.

            SimpleHTTPRequestHandler's inherited implementation otherwise
            exposes the existence and size of the DB and source files.
            """
            route = urlparse(self.path).path
            rewritten = static_route(route)
            if rewritten is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.path = rewritten
            return super().do_HEAD()

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

            if route == "/api/export.csv":
                node, n = q.get("node"), limit("n", 1000, 10000)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM uplinks WHERE "
                        "(?1 IS NULL OR device_id = ?1) ORDER BY "
                        "received_at DESC, id DESC LIMIT ?2) "
                        "ORDER BY received_at ASC, id ASC", (node, n)
                    ).fetchall()
                fields = ("received_at", "device_id", "fcnt", "rssi", "snr",
                          "sf", "n_pest", "soil_safe", "soil_fault",
                          "camera_fault", "infer_ready", "lockout_active",
                          "soil_vwc_pct", "batt_mv", "action",
                          "sprays_today", "raw_hex")
                output = io.StringIO(newline="")
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(fields)
                writer.writerows([r[f] for f in fields] for r in rows)
                body = output.getvalue().encode("utf-8-sig")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                                 "attachment; filename=bananaguard-telemetry.csv")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if route.startswith("/api/"):
                return self._json({"error": "unknown endpoint"}, 404)

            rewritten = static_route(route)
            if rewritten is None:
                return self._json({"error": "not found"}, 404)
            self.path = rewritten
            return super().do_GET()

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="listen address (default: localhost only)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    conn = open_db(args.db)
    token = os.environ.get("BG_WEBHOOK_TOKEN")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not token:
        ap.error("BG_WEBHOOK_TOKEN is required when --host is not localhost")
    httpd = ThreadingHTTPServer((args.host, args.port),
                                make_handler(conn, token))
    print(f"BananaGuard backend on http://{args.host}:{args.port} "
          f"(db: {args.db}, webhook auth: {'ON' if token else 'off'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
