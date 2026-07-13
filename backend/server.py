#!/usr/bin/env python3
"""BananaGuard backend — TTN webhook listener + SQLite log + dashboard API.

Zero dependencies (Python 3.9+ stdlib only), one file, one process:

  * POST /ttn          — The Things Stack v3 field webhook. Uses decoded_payload
                         when the console formatter ran, otherwise decodes
                         the raw frm_payload itself (decode_payload.py).
                         Required shared secret: set BG_WEBHOOK_TOKEN and
                         configure the same value as an additional webhook
                         header `X-Webhook-Token` in the TTN console.
  * POST /demo/ttn     — localhost-only simulator ingest (opt-in)
  * GET  /api/meta     — source/capability contract for the dashboard
  * GET  /api/health   — liveness + row count
  * GET  /api/nodes    — latest uplink per device
  * GET  /api/state?node=ID          — latest uplink + derived fields
  * GET  /api/history?node=ID&n=64   — recent uplinks (oldest first)
  * GET  /api/logs?node=ID&n=50      — uplinks rendered as event entries
  * GET  /api/export.csv?node=ID     — validation-ready telemetry export
  * GET  /…            — static files from the repo root (the dashboard)

Run:
    python3 backend/server.py [--port 8000] [--db backend/bananaguard.db]

Exercise simulator provenance without hardware:
    python3 backend/server.py --allow-simulator
    python3 backend/simulate_uplinks.py --once     # or --interval 5
    curl 'http://localhost:8000/api/nodes?source=simulator'

The dashboard's visual Demo is in-page and never consumes simulator records.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import hmac
import ipaddress
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from decode_payload import PayloadError, decode_uplink

MAX_WEBHOOK_BYTES = 64 * 1024
API_VERSION = "1"
SCHEMA_VERSION = 4
PAYLOAD_VERSION = 1
N_EIL = 5
WINDOW_MINUTES = 30
STALE_AFTER_SECONDS = 90 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60
MIN_WEBHOOK_TOKEN_BYTES = 32
SOURCE_KINDS = ("field", "simulator", "legacy_unknown")
QUERYABLE_SOURCES = ("field", "simulator")
DEVICE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,34}[a-z0-9])?$")
APPLICATION_ID_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")

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
    event_key      TEXT,
    source_kind    TEXT NOT NULL DEFAULT 'legacy_unknown'
                   CHECK (source_kind IN ('field','simulator','legacy_unknown')),
    ingested_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_version INTEGER,
    application_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_uplinks_dev_time
    ON uplinks (device_id, received_at DESC);
CREATE TABLE IF NOT EXISTS nodes (
    device_id   TEXT PRIMARY KEY,
    display_name TEXT,
    block       TEXT,
    latitude    REAL CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude   REAL CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""

_db_lock = threading.Lock()


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    applied = {r["version"] for r in conn.execute(
        "SELECT version FROM schema_migrations")}

    def columns(table: str) -> set[str]:
        return {r["name"] for r in conn.execute(
            f"PRAGMA table_info({table})")}

    def finish(version: int):
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (?, ?)", (version, utcnow()))

    # Migration 1: webhook retry de-duplication. The column check makes this
    # safe for databases created by intermediate development builds.
    if 1 not in applied:
        if "event_key" not in columns("uplinks"):
            conn.execute("ALTER TABLE uplinks ADD COLUMN event_key TEXT")
        conn.execute("DROP INDEX IF EXISTS idx_uplinks_event_key")
        conn.execute("CREATE UNIQUE INDEX idx_uplinks_event_key "
                     "ON uplinks (event_key)")
        finish(1)

    # Migration 2: data provenance. Existing observations are deliberately
    # labelled legacy_unknown; guessing that historical rows were field data
    # would let simulations leak into Actual mode.
    if 2 not in applied:
        existing = columns("uplinks")
        if "source_kind" not in existing:
            conn.execute("ALTER TABLE uplinks ADD COLUMN source_kind TEXT "
                         "NOT NULL DEFAULT 'legacy_unknown'")
        if "ingested_at" not in existing:
            conn.execute("ALTER TABLE uplinks ADD COLUMN ingested_at TEXT")
        if "payload_version" not in existing:
            conn.execute("ALTER TABLE uplinks ADD COLUMN payload_version INTEGER")
        if "application_id" not in existing:
            conn.execute("ALTER TABLE uplinks ADD COLUMN application_id TEXT")
        migrated_at = utcnow()
        conn.execute("UPDATE uplinks SET source_kind = 'legacy_unknown' "
                     "WHERE source_kind IS NULL OR source_kind NOT IN "
                     "('field','simulator','legacy_unknown')")
        conn.execute("UPDATE uplinks SET ingested_at = ? "
                     "WHERE ingested_at IS NULL OR ingested_at = ''",
                     (migrated_at,))
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS uplinks_source_insert
            BEFORE INSERT ON uplinks
            WHEN NEW.source_kind NOT IN ('field','simulator','legacy_unknown')
            BEGIN SELECT RAISE(ABORT, 'invalid source_kind'); END;
            CREATE TRIGGER IF NOT EXISTS uplinks_source_update
            BEFORE UPDATE OF source_kind ON uplinks
            WHEN NEW.source_kind NOT IN ('field','simulator','legacy_unknown')
            BEGIN SELECT RAISE(ABORT, 'invalid source_kind'); END;
            CREATE INDEX IF NOT EXISTS idx_uplinks_source_dev_time
                ON uplinks (source_kind, device_id, received_at DESC);
        """)
        finish(2)

    # Migration 3: operator-managed node names and locations. There is no
    # HTTP write route; the local CLI is the only management surface.
    if 3 not in applied:
        conn.execute("""CREATE TABLE IF NOT EXISTS nodes (
            device_id TEXT PRIMARY KEY,
            display_name TEXT,
            block TEXT,
            latitude REAL CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
            longitude REAL CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
        )""")
        finish(3)

    # Migration 4: retries deduplicate within a provenance source. Keeping the
    # original frame key and moving source_kind into the unique index preserves
    # retry behavior for pre-v4 databases while allowing a simulator frame to
    # coexist with an otherwise-identical field frame.
    if 4 not in applied:
        conn.execute("DROP INDEX IF EXISTS idx_uplinks_event_key")
        conn.execute(
            "CREATE UNIQUE INDEX idx_uplinks_source_event_key "
            "ON uplinks (source_kind, event_key)")
        finish(4)

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def norm_time(ts, *, now: datetime | None = None,
              require_explicit: bool = False) -> str:
    """Normalize any RFC3339 timestamp to UTC with fixed microsecond
    precision so lexicographic order in SQLite equals chronological
    order ('...00Z' would otherwise sort AFTER '...00.500Z'). Simulator
    messages may fall back to receipt time, but field evidence must carry an
    explicit offset-bearing source timestamp. Malformed supplied timestamps
    are always rejected so validation evidence is never silently re-dated."""
    now = now or datetime.now(timezone.utc)
    if ts is None or ts == "":
        if require_explicit:
            raise PayloadError(
                "field received_at must be an offset-bearing RFC3339 timestamp")
        return now.isoformat(timespec="microseconds")
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise PayloadError("received_at must be an RFC3339 timestamp")
    if dt.tzinfo is None:
        if require_explicit:
            raise PayloadError(
                "field received_at must include a UTC offset or Z suffix")
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if (dt - now).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        raise PayloadError(
            f"received_at is more than {MAX_FUTURE_SKEW_SECONDS} seconds "
            "in the future")
    return dt.isoformat(timespec="microseconds")


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
    version = decoded.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise PayloadError(f"unknown payload version {version!r}")
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


def _event_key(application_id: str | None, device_id: str, up: dict,
               received_at: str) -> str | None:
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
    app = application_id or "unknown-application"
    if session:
        source = f"session:{app}:{device_id}:{session}:{fcnt}"
    elif source_time:
        source = f"time:{app}:{device_id}:{received_at}:{fcnt}"
    else:
        return None
    return hashlib.sha256(source.encode()).hexdigest()


# Single Li-ion cell: 3.30 V empty (BG_BATT_CRITICAL_MV) … 4.20 V full.
def batt_pct(mv: int) -> float:
    return round(max(0.0, min(1.0, (mv - 3300) / 900.0)) * 100, 1)


def extract_uplink(msg: dict, *, source_kind: str = "field") -> dict:
    """Pull the fields we store out of a TTS v3 webhook body."""
    if source_kind not in QUERYABLE_SOURCES:
        raise PayloadError(f"invalid ingest source {source_kind!r}")
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
    app_ids = ids.get("application_ids") or {}
    if not isinstance(app_ids, dict):
        raise PayloadError("application_ids must be an object")
    application_id = app_ids.get("application_id")
    if (application_id is not None and
            (not isinstance(application_id, str) or
             not APPLICATION_ID_RE.fullmatch(application_id))):
        raise PayloadError("invalid application_id")
    if source_kind == "field" and application_id is None:
        raise PayloadError("missing application_id for field uplink")
    fcnt = _int_field(
        up, "f_cnt", 0, 0xFFFFFFFF, optional=source_kind != "field")

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
    ingested = datetime.now(timezone.utc)
    received_at = norm_time(
        up.get("received_at"), now=ingested,
        require_explicit=source_kind == "field")
    return {
        "received_at": received_at,
        "ingested_at": ingested.isoformat(timespec="microseconds"),
        "source_kind": source_kind,
        "payload_version": PAYLOAD_VERSION,
        "application_id": application_id,
        "device_id": dev,
        "fport": _int_field(up, "f_port", 0, 255, optional=True),
        "fcnt": fcnt,
        "rssi": _number_field(rx, "rssi", -250, 50),
        "snr": _number_field(rx, "snr", -100, 100),
        "sf": _int_field(lora, "spreading_factor", 5, 12,
                         optional=True),
        "raw_hex": raw_hex,
        "event_key": _event_key(application_id, dev, up, received_at),
        **{k: decoded.get(k) for k in (
            "n_pest", "soil_safe", "soil_fault", "camera_fault",
            "infer_ready", "lockout_active", "soil_vwc_pct", "batt_mv",
            "action", "sprays_today")},
    }


def insert_uplink(conn: sqlite3.Connection, u: dict) -> tuple[int, bool]:
    cols = ("received_at device_id fport fcnt rssi snr sf n_pest soil_safe "
            "soil_fault camera_fault infer_ready lockout_active soil_vwc_pct "
            "batt_mv action sprays_today raw_hex event_key source_kind "
            "ingested_at payload_version application_id").split()
    with _db_lock:
        cur = conn.execute(
            f"INSERT INTO uplinks ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))}) "
            "ON CONFLICT(source_kind, event_key) DO NOTHING",
            [u.get(c) for c in cols])
        conn.commit()
        if cur.rowcount:
            return cur.lastrowid, True
        row = conn.execute(
            "SELECT * FROM uplinks WHERE source_kind = ? AND event_key = ?",
            (u["source_kind"], u["event_key"])).fetchone()
        evidence_fields = ("device_id", "fcnt", "n_pest", "soil_safe",
                           "soil_fault", "camera_fault", "infer_ready",
                           "lockout_active", "soil_vwc_pct", "batt_mv",
                           "action", "sprays_today", "raw_hex", "source_kind",
                           "payload_version", "application_id")
        if any(row[k] != u.get(k) for k in evidence_fields):
            raise PayloadError("duplicate frame key conflicts with stored payload")
    return row["id"], False


def safety_violation(r) -> bool:
    return bool(r["action"] == "SPRAY" and
                (not r["soil_safe"] or r["soil_fault"] or
                 r["camera_fault"] or not r["infer_ready"] or
                 r["lockout_active"]))


def seconds_since(ts, *, now: datetime | None = None) -> int:
    """Return a server-calculated, non-negative timestamp age."""
    try:
        observed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return max(0, int((now - observed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return STALE_AFTER_SECONDS + 1


def demand_held(r) -> bool:
    """True when pressure crossed EIL but the soil gate is unsafe.

    Firmware may correctly emit LOG instead of LOCKOUT for this case. The
    operator view still needs to say that treatment was held rather than
    implying an ordinary observation.
    """
    return bool(r["n_pest"] > N_EIL and not r["soil_safe"] and
                not r["soil_fault"] and r["action"] != "SPRAY")


def row_to_state(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("event_key", None)
    d["batt_pct"] = batt_pct(d["batt_mv"])
    violation = safety_violation(r)
    held = demand_held(r)
    fault = (d["soil_fault"] or d["camera_fault"] or
             not d["infer_ready"] or d["action"] == "FAULT" or violation)
    if fault:
        status = "fault"
    elif held:
        status = "held"
    elif d["action"] == "SPRAY":
        # An uplink is a completed/reported cycle, never proof that an
        # actuator is still energised at request time.
        status = "reported"
    elif d["action"] == "LOCKOUT" or d["lockout_active"]:
        status = "blocked"
    elif d["n_pest"] > N_EIL:
        status = "watch"
    else:
        status = "clear"
    d["status"] = status
    d["safety_violation"] = violation
    d["treatment_held"] = held
    d["action_status"] = ("completed" if d["action"] == "SPRAY" and
                          not violation else "reported")
    d["eil_threshold"] = N_EIL
    d["window_minutes"] = WINDOW_MINUTES
    now = datetime.now(timezone.utc)
    age = seconds_since(d["received_at"], now=now)
    d["source_time"] = d["received_at"]
    d["source_age_seconds"] = age
    d["ingestion_age_seconds"] = seconds_since(d.get("ingested_at"), now=now)
    d["age_seconds"] = age
    d["online"] = age <= STALE_AFTER_SECONDS
    for k in ("soil_safe", "soil_fault", "camera_fault", "infer_ready",
              "lockout_active"):
        d[k] = bool(d[k])
    if "registered" in d:
        d["registered"] = bool(d["registered"])
    if "registry_enabled" in d:
        d["registry_enabled"] = bool(d["registry_enabled"])
    return d


LOG_STYLES = {
    "SPRAY":   ("spray",   "sprinkler",    "act"),
    "LOCKOUT": ("blocked", "lock_clock",   "warn"),
    "FAULT":   ("fault",   "error",        "warn"),
    "LOG":     ("detect",  "pest_control", "info"),
}


def row_to_log(r: sqlite3.Row) -> dict:
    violation = safety_violation(r)
    held = demand_held(r)
    reported_fault = bool(r["soil_fault"] or r["camera_fault"] or
                          not r["infer_ready"] or r["action"] == "FAULT")
    typ, icon, sev = (("fault", "report", "bad") if violation else
                      ("fault", "error", "warn") if reported_fault else
                      ("held", "water_drop", "warn") if held else
                      LOG_STYLES.get(r["action"], LOG_STYLES["LOG"]))
    vwc = r["soil_vwc_pct"]
    soil = "fault" if vwc is None else f"{vwc}%"
    if violation:
        title = "Safety invariant violated — inspect node"
    elif reported_fault:
        title = "Sensor fault — spray disabled"
    elif held:
        title = "Treatment held — soil gate unsafe"
    elif r["action"] == "SPRAY":
        title = "Spray cycle reported complete"
    elif r["action"] == "LOCKOUT":
        title = "Spray inhibited — lockout"
    elif r["n_pest"] > 0:
        title = f"{r['n_pest']} detection(s) in window"
    else:
        title = "Cycle report — no detections"
    return {
        "time": r["received_at"], "node": r["device_id"], "type": typ,
        "icon": icon, "sev": sev, "title": title,
        "source_time": r["received_at"],
        "ingested_at": r["ingested_at"],
        "source_kind": r["source_kind"],
        "payload_version": r["payload_version"],
        "application_id": r["application_id"],
        "source_age_seconds": seconds_since(r["received_at"]),
        "ingestion_age_seconds": seconds_since(r["ingested_at"]),
        "action_status": ("completed" if r["action"] == "SPRAY" and
                          not violation else "reported"),
        "treatment_held": held,
        "detail": (f"N̂_pest {r['n_pest']} · soil {soil} · "
                   f"{r['batt_mv']} mV · sprays today {r['sprays_today']}"),
    }


UPLINK_WITH_NODE = """u.*,
    COALESCE(NULLIF(TRIM(n.display_name), ''), u.device_id) AS display_name,
    n.block AS block, n.latitude AS latitude, n.longitude AS longitude,
    COALESCE(n.enabled, 1) AS registry_enabled,
    CASE WHEN n.device_id IS NULL THEN 0 ELSE 1 END AS registered"""


def is_loopback_host(host: str) -> bool:
    """Return whether an HTTP peer is local, including IPv4-mapped IPv6."""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return host.lower() == "localhost"


# Static allowlist: ONLY the dashboard files, never the repo tree (which
# would expose the SQLite DB, firmware sources and keys on a public host).
STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/support.js": "support.js", "/Ring.dc.html": "Ring.dc.html",
                "/LICENSE": "LICENSE"}
STATIC_FONT_FILES = {
    "/vendor/fonts/fonts.css",
    "/vendor/fonts/LICENSES.txt",
    "/vendor/fonts/manrope-latin.woff2",
    "/vendor/fonts/material-symbols-rounded.woff2",
    "/vendor/fonts/space-grotesk-latin.woff2",
}
STATIC_LEGAL_FILES = {"/vendor/LICENSES.txt"}


def static_route(route: str):
    """Return the rewritten URL path for an allowed static route, else
    None. Works on URL paths only — never reconstructs a path from a
    resolved filesystem path, because on Windows resolve() can expand
    8.3 short names (e.g. inside the PyInstaller temp dir) into a form
    that no longer matches REPO_ROOT."""
    if route in STATIC_FILES:
        return "/" + STATIC_FILES[route]
    if route in STATIC_FONT_FILES or route in STATIC_LEGAL_FILES:
        return route
    if route.startswith("/vendor/"):
        p = (REPO_ROOT / route.lstrip("/")).resolve()
        vendor = (REPO_ROOT / "vendor").resolve()
        if p.is_file() and p.suffix == ".js" and vendor in p.parents:
            return route
    return None


def valid_webhook_token(token: str | None) -> bool:
    return (isinstance(token, str) and
            len(token.encode("utf-8")) >= MIN_WEBHOOK_TOKEN_BYTES)


def make_handler(conn: sqlite3.Connection, token: str | None,
                 *, allow_simulator: bool = False,
                 field_ready: bool = False):

    token = token if valid_webhook_token(token) else None

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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---------------- webhook ----------------
        def do_POST(self):
            route = urlparse(self.path).path
            if route not in ("/ttn", "/demo/ttn"):
                return self._json({"error": "unknown endpoint"}, 404)
            if route == "/demo/ttn":
                if not allow_simulator:
                    return self._json({"error": "unknown endpoint"}, 404)
                if not is_loopback_host(self.client_address[0]):
                    return self._json(
                        {"error": "simulator ingest is localhost-only"}, 403)
                source_kind = "simulator"
            else:
                if not token:
                    return self._json(
                        {"error": "field ingest disabled: BG_WEBHOOK_TOKEN "
                                  "is not configured"}, 503)
                supplied = self.headers.get("X-Webhook-Token", "")
                if not hmac.compare_digest(supplied, token):
                    return self._json({"error": "bad token"}, 403)
                source_kind = "field"
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
                u = extract_uplink(msg, source_kind=source_kind)
                rid, inserted = insert_uplink(conn, u)
            except (PayloadError, json.JSONDecodeError, UnicodeError, KeyError,
                    TypeError, OverflowError) as e:
                return self._json({"error": str(e)}, 400)
            self._json({"ok": True, "id": rid, "device_id": u["device_id"],
                        "action": u["action"], "source_kind": source_kind,
                        "duplicate": not inserted})

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
                    source_counts = {r["source_kind"]: r["c"] for r in
                                     conn.execute("SELECT source_kind, COUNT(*) c "
                                                  "FROM uplinks GROUP BY "
                                                  "source_kind")}
                n = sum(source_counts.values())
                return self._json({"ok": True, "uplinks": n,
                                   "sources": {k: source_counts.get(k, 0)
                                               for k in SOURCE_KINDS}})

            if route == "/api/meta":
                with _db_lock:
                    source_counts = {r["source_kind"]: r["c"] for r in
                                     conn.execute("SELECT source_kind, COUNT(*) c "
                                                  "FROM uplinks GROUP BY "
                                                  "source_kind")}
                    registered = conn.execute(
                        "SELECT COUNT(*) c FROM nodes WHERE enabled = 1"
                    ).fetchone()["c"]
                    visible_field_nodes = conn.execute(
                        "SELECT COUNT(DISTINCT u.device_id) c FROM uplinks u "
                        "LEFT JOIN nodes n ON n.device_id = u.device_id "
                        "WHERE u.source_kind = 'field' AND "
                        "COALESCE(n.enabled, 1) = 1"
                    ).fetchone()["c"]
                counts = {k: source_counts.get(k, 0) for k in SOURCE_KINDS}
                counts["uplinks"] = sum(source_counts.values())
                counts["nodes"] = visible_field_nodes
                counts["registered_nodes"] = registered
                profile = "demo" if allow_simulator else "field"
                return self._json({
                    "api_version": API_VERSION,
                    "server_time": utcnow(),
                    "backend_profile": profile,
                    "profile": profile,
                    "read_only": True,
                    "field_ingest_configured": bool(token),
                    "stale_after_seconds": STALE_AFTER_SECONDS,
                    "supported_payload_versions": [PAYLOAD_VERSION],
                    "capabilities": {
                        "telemetry": True,
                        "history": True,
                        "export": True,
                        "remote_commands": False,
                        "simulator_ingest": allow_simulator,
                        "node_registry": True,
                    },
                    "field_ready": bool(field_ready and token),
                    "counts": counts,
                })

            def limit(key, default, cap):
                try:
                    return max(1, min(int(q.get(key, default)), cap))
                except ValueError:
                    return default

            data_routes = ("/api/nodes", "/api/state", "/api/history",
                           "/api/logs", "/api/export.csv")
            source = q.get("source", "field")
            if route in data_routes and source not in QUERYABLE_SOURCES:
                return self._json(
                    {"error": "source must be field or simulator"}, 400)

            # "Latest" is by uplink time (then id as tie-break), not by
            # insertion order — a TTN redelivery of an old frame must not
            # regress the dashboard to stale telemetry.
            if route == "/api/nodes":
                with _db_lock:
                    rows = conn.execute(
                        f"SELECT {UPLINK_WITH_NODE} FROM uplinks u "
                        "LEFT JOIN nodes n ON n.device_id = u.device_id "
                        "JOIN (SELECT device_id, "
                        "MAX(received_at || printf('#%012d', id)) mk "
                        "FROM uplinks WHERE source_kind = ?1 "
                        "GROUP BY device_id) t ON "
                        "u.device_id = t.device_id AND "
                        "u.received_at || printf('#%012d', u.id) = t.mk "
                        "WHERE u.source_kind = ?1 AND COALESCE(n.enabled, 1) = 1 "
                        "ORDER BY u.device_id", (source,)).fetchall()
                return self._json([row_to_state(r) for r in rows])

            if route == "/api/state":
                node = q.get("node")
                with _db_lock:
                    r = conn.execute(
                        f"SELECT {UPLINK_WITH_NODE} FROM uplinks u "
                        "LEFT JOIN nodes n ON n.device_id = u.device_id "
                        "WHERE (?1 IS NULL OR u.device_id = ?1) "
                        "AND u.source_kind = ?2 AND COALESCE(n.enabled, 1) = 1 "
                        "ORDER BY u.received_at DESC, u.id DESC LIMIT 1",
                        (node, source)).fetchone()
                if not r:
                    return self._json({"error": "no data yet"}, 404)
                return self._json(row_to_state(r))

            if route == "/api/history":
                node, n = q.get("node"), limit("n", 64, 1000)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM uplinks WHERE "
                        "(?1 IS NULL OR device_id = ?1) AND source_kind = ?3 "
                        "AND NOT EXISTS (SELECT 1 FROM nodes WHERE "
                        "nodes.device_id = uplinks.device_id AND enabled = 0) "
                        "ORDER BY received_at DESC, id DESC LIMIT ?2) "
                        "ORDER BY received_at ASC, id ASC",
                        (node, n, source)).fetchall()
                return self._json([row_to_state(r) for r in rows])

            if route == "/api/logs":
                node, n = q.get("node"), limit("n", 50, 500)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM uplinks WHERE (?1 IS NULL OR "
                        "device_id = ?1) AND source_kind = ?3 AND NOT EXISTS "
                        "(SELECT 1 FROM nodes WHERE nodes.device_id = "
                        "uplinks.device_id AND enabled = 0) "
                        "ORDER BY received_at DESC, id DESC LIMIT ?2",
                        (node, n, source)).fetchall()
                return self._json([row_to_log(r) for r in rows])

            if route == "/api/export.csv":
                node, n = q.get("node"), limit("n", 1000, 10000)
                with _db_lock:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM uplinks WHERE "
                        "(?1 IS NULL OR device_id = ?1) AND source_kind = ?3 "
                        "AND NOT EXISTS (SELECT 1 FROM nodes WHERE "
                        "nodes.device_id = uplinks.device_id AND enabled = 0) "
                        "ORDER BY received_at DESC, id DESC LIMIT ?2) "
                        "ORDER BY received_at ASC, id ASC", (node, n, source)
                    ).fetchall()
                fields = ("received_at", "ingested_at", "source_kind",
                          "payload_version", "application_id", "device_id",
                          "fcnt", "rssi", "snr",
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
    ap.add_argument(
        "--allow-simulator", action="store_true",
        help="enable localhost-only POST /demo/ttn")
    ap.add_argument(
        "--field-ready", action="store_true",
        help="operator attestation that physical field-readiness gates passed")
    args = ap.parse_args()

    token = os.environ.get("BG_WEBHOOK_TOKEN")
    if not is_loopback_host(args.host):
        ap.error(
            "non-loopback binding is disabled; terminate authenticated HTTPS "
            "in a reverse proxy and forward only to localhost")
    if token and not valid_webhook_token(token):
        ap.error(
            f"BG_WEBHOOK_TOKEN must contain at least "
            f"{MIN_WEBHOOK_TOKEN_BYTES} UTF-8 bytes")
    effective_field_ready = bool(args.field_ready and token)
    conn = open_db(args.db)
    httpd = ThreadingHTTPServer((args.host, args.port),
                                make_handler(
                                    conn, token,
                                    allow_simulator=args.allow_simulator,
                                    field_ready=effective_field_ready))
    print(f"BananaGuard backend on http://{args.host}:{args.port} "
          f"(db: {args.db}, webhook auth: {'ON' if token else 'off'}, "
          f"simulator ingest: {'ON' if args.allow_simulator else 'off'}, "
          f"field ready: {'YES' if effective_field_ready else 'no'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
