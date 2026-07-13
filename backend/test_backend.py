#!/usr/bin/env python3
"""Backend integration tests — stdlib unittest, in-process server on an
ephemeral port, temp database. Run: python3 backend/test_backend.py
"""
import base64
import csv
import http.client
import io
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone

import decode_payload as dp
import server as srv


def post(url, body, headers=None):
    req = urllib.request.Request(
        url, json.dumps(body).encode(),
        {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, json.loads(r.read())


_TTS_FRAME_COUNTER = itertools.count(1)


def tts_body(dev, state, decoded=False):
    up = {
        "f_port": 1, "f_cnt": next(_TTS_FRAME_COUNTER),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "frm_payload": base64.b64encode(dp.encode_uplink(state)).decode(),
        "rx_metadata": [{"rssi": -97, "snr": 8.2}],
        "settings": {"data_rate": {"lora": {"spreading_factor": 10}}},
    }
    if decoded:
        up["decoded_payload"] = dict(state, version=1)
    return {"end_device_ids": {
        "device_id": dev,
        "application_ids": {"application_id": "bananaguard"},
    }, "uplink_message": up}


STATE = {"n_pest": 7, "soil_safe": True, "soil_fault": False,
         "camera_fault": False, "infer_ready": True, "lockout_active": False,
         "soil_vwc_pct": 38, "batt_mv": 3921, "action": "SPRAY",
         "sprays_today": 2}
TEST_TOKEN = "test-bananaguard-webhook-token-0123456789"


class BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = srv.open_db(":memory:")
        cls.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), srv.make_handler(cls.conn, token=TEST_TOKEN))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.demo_httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), srv.make_handler(
                cls.conn, token=TEST_TOKEN, allow_simulator=True))
        cls.demo_base = (
            f"http://127.0.0.1:{cls.demo_httpd.server_address[1]}")
        cls.no_token_httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), srv.make_handler(
                cls.conn, token=None, field_ready=True))
        cls.no_token_base = (
            f"http://127.0.0.1:{cls.no_token_httpd.server_address[1]}")
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=cls.demo_httpd.serve_forever,
                         daemon=True).start()
        threading.Thread(target=cls.no_token_httpd.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.demo_httpd.shutdown()
        cls.demo_httpd.server_close()
        cls.no_token_httpd.shutdown()
        cls.no_token_httpd.server_close()
        cls.conn.close()

    def hook(self, body):
        return post(self.base + "/ttn", body,
                    {"X-Webhook-Token": TEST_TOKEN})

    def demo_hook(self, body):
        return post(self.demo_base + "/demo/ttn", body)

    def test_01_auth_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.base + "/ttn", tts_body("bg-n01", STATE))
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_02_webhook_raw_payload(self):
        status, out = self.hook(tts_body("bg-n01", STATE))
        self.assertEqual(status, 200)
        self.assertEqual(out["action"], "SPRAY")

    def test_03_webhook_decoded_payload(self):
        status, out = self.hook(tts_body("bg-n02", dict(
            STATE, n_pest=1, action="LOG", soil_vwc_pct=64,
            soil_safe=False), decoded=True))
        self.assertEqual(status, 200)
        self.assertEqual(out["device_id"], "bg-n02")

    def test_04_webhook_fault_payload(self):
        st = dict(STATE, soil_vwc_pct=None, soil_fault=True, action="FAULT",
                  n_pest=0)
        status, out = self.hook(tts_body("bg-n03", st))
        self.assertEqual(status, 200)
        self.assertEqual(out["action"], "FAULT")
        combined = dict(STATE, camera_fault=True, soil_safe=False,
                        soil_vwc_pct=72, n_pest=8, action="LOG")
        self.hook(tts_body("bg-fault-held", combined))
        _, logs = get(self.base + "/api/logs?node=bg-fault-held&n=1")
        self.assertEqual(logs[0]["type"], "fault")
        self.assertIn("Sensor fault", logs[0]["title"])
        self.assertNotIn("held", logs[0]["title"].lower())

    def test_05_bad_payload_rejected(self):
        body = tts_body("bg-n01", STATE)
        body["uplink_message"]["frm_payload"] = base64.b64encode(
            b"\x02\x00\x00").decode()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(body)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_06_state_and_derivations(self):
        _, s = get(self.base + "/api/state?node=bg-n01")
        self.assertEqual(s["n_pest"], 7)
        self.assertEqual(s["status"], "reported")
        self.assertEqual(s["action_status"], "completed")
        self.assertEqual(s["batt_pct"], 69.0)  # (3921-3300)/900
        self.assertEqual(s["sf"], 10)
        self.assertTrue(s["online"])
        self.assertNotIn("event_key", s)
        self.assertEqual(s["source_kind"], "field")
        self.assertEqual(s["payload_version"], 1)
        self.assertEqual(s["application_id"], "bananaguard")
        self.assertEqual(s["source_time"], s["received_at"])
        self.assertIn("ingested_at", s)
        self.assertGreaterEqual(s["source_age_seconds"], 0)
        self.assertGreaterEqual(s["ingestion_age_seconds"], 0)
        _, s3 = get(self.base + "/api/state?node=bg-n03")
        self.assertEqual(s3["status"], "fault")
        self.assertIsNone(s3["soil_vwc_pct"])

    def test_07_nodes_latest_per_device(self):
        _, nodes = get(self.base + "/api/nodes")
        self.assertEqual([n["device_id"] for n in nodes],
                         ["bg-fault-held", "bg-n01", "bg-n02", "bg-n03"])

    def test_08_history_and_logs(self):
        for i in range(5):
            self.hook(tts_body("bg-n01", dict(STATE, n_pest=i, action="LOG")))
        _, hist = get(self.base + "/api/history?node=bg-n01&n=3")
        self.assertEqual(len(hist), 3)
        self.assertEqual([h["n_pest"] for h in hist], [2, 3, 4])  # oldest first
        _, logs = get(self.base + "/api/logs?node=bg-n01&n=2")
        self.assertEqual(len(logs), 2)
        self.assertIn("N̂_pest", logs[0]["detail"])

    def test_09_health_and_404(self):
        _, h = get(self.base + "/api/health")
        self.assertTrue(h["ok"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.base + "/api/nope")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

    def test_10_decoder_and_firmware_threshold_contracts(self):
        self.assertEqual(dp.decode_uplink(dp.encode_uplink(STATE)),
                         dict(STATE, version=1))
        config = (srv.REPO_ROOT / "firmware" / "main" /
                  "app_config.h").read_text(encoding="utf-8")

        def macro(name):
            match = re.search(
                rf"^\s*#define\s+{re.escape(name)}\s+(\d+)\b",
                config, re.MULTILINE)
            self.assertIsNotNone(match, f"missing firmware macro {name}")
            return int(match.group(1))

        # Actual-mode held/watch semantics must not drift from firmware Eq. 2.
        self.assertEqual(srv.N_EIL, macro("BG_N_EIL"))
        self.assertEqual(srv.WINDOW_MINUTES,
                         macro("BG_AGG_WINDOW_MIN"))

    def test_11_partial_decoded_payload_falls_back_to_raw(self):
        body = tts_body("bg-n04", STATE)
        # formatter emitted only n_pest — must fall back to frm_payload
        body["uplink_message"]["decoded_payload"] = {"n_pest": 3}
        status, out = self.hook(body)
        self.assertEqual(status, 200)
        _, s = get(self.base + "/api/state?node=bg-n04")
        self.assertEqual(s["n_pest"], STATE["n_pest"])  # raw won
        self.assertEqual(s["batt_mv"], STATE["batt_mv"])

    def test_12_negative_limit_clamped(self):
        _, hist = get(self.base + "/api/history?node=bg-n01&n=-1")
        self.assertEqual(len(hist), 1)
        _, logs = get(self.base + "/api/logs?node=bg-n01&n=-5")
        self.assertEqual(len(logs), 1)

    def test_13_static_allowlist_only(self):
        _, _ = get(self.base + "/api/health")  # server alive
        for blocked in ("/backend/server.py", "/backend/bananaguard.db",
                        "/firmware/main/app_config.h", "/README.md",
                        "/vendor/../backend/server.py"):
            with self.assertRaises(urllib.error.HTTPError, msg=blocked) as cm:
                get(self.base + blocked)
            self.assertEqual(cm.exception.code, 404, blocked)
            cm.exception.close()
        with urllib.request.urlopen(self.base + "/index.html") as r:
            self.assertEqual(r.status, 200)
        with urllib.request.urlopen(
                self.base + "/vendor/react.production.min.js") as r:
            self.assertEqual(r.status, 200)
        with urllib.request.urlopen(
                self.base + "/vendor/fonts/LICENSES.txt") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("SIL OPEN FONT LICENSE", r.read().decode("utf-8"))
        with urllib.request.urlopen(self.base + "/LICENSE") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("MIT License", r.read().decode("utf-8"))
        with urllib.request.urlopen(self.base + "/vendor/LICENSES.txt") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("Copyright (c) Facebook", r.read().decode("utf-8"))

    def test_14_stale_redelivery_does_not_regress_state(self):
        newer = tts_body("bg-n05", dict(STATE, n_pest=9))
        newer["uplink_message"]["received_at"] = "2026-07-05T10:00:00+00:00"
        older = tts_body("bg-n05", dict(STATE, n_pest=1))
        older["uplink_message"]["received_at"] = "2026-07-05T08:00:00+00:00"
        self.hook(newer)
        self.hook(older)   # TTN redelivery arrives late
        _, s = get(self.base + "/api/state?node=bg-n05")
        self.assertEqual(s["n_pest"], 9)
        _, nodes = get(self.base + "/api/nodes")
        n05 = next(n for n in nodes if n["device_id"] == "bg-n05")
        self.assertEqual(n05["n_pest"], 9)

    def test_15_mixed_precision_timestamps_order_chronologically(self):
        # Raw text ordering would put "...00Z" AFTER "...00.500Z"; the
        # server must normalize precision so the .500 frame stays latest.
        newer = tts_body("bg-n06", dict(STATE, n_pest=7))
        newer["uplink_message"]["received_at"] = "2026-07-05T10:00:00.500Z"
        older = tts_body("bg-n06", dict(STATE, n_pest=2))
        older["uplink_message"]["received_at"] = "2026-07-05T10:00:00Z"
        self.hook(newer)
        self.hook(older)
        _, s = get(self.base + "/api/state?node=bg-n06")
        self.assertEqual(s["n_pest"], 7)
        # nanosecond input (TTN native) must not 500
        nano = tts_body("bg-n06", dict(STATE, n_pest=4))
        nano["uplink_message"]["received_at"] = "2026-07-05T10:00:01.123456789Z"
        status, _ = self.hook(nano)
        self.assertEqual(status, 200)
        _, s = get(self.base + "/api/state?node=bg-n06")
        self.assertEqual(s["n_pest"], 4)

    def test_16_formatter_values_are_validated_and_cross_checked(self):
        poisoned = tts_body("bg-n07", STATE, decoded=True)
        del poisoned["uplink_message"]["frm_payload"]
        poisoned["uplink_message"]["decoded_payload"]["n_pest"] = "many"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(poisoned)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

        for bad_version in (True, 1.0, "1", None):
            poisoned = tts_body("bg-n07-version", STATE, decoded=True)
            del poisoned["uplink_message"]["frm_payload"]
            poisoned["uplink_message"]["decoded_payload"]["version"] = bad_version
            with self.subTest(version=bad_version), self.assertRaises(
                    urllib.error.HTTPError) as cm:
                self.hook(poisoned)
            self.assertEqual(cm.exception.code, 400)
            cm.exception.close()

        mismatch = tts_body("bg-n07", STATE, decoded=True)
        mismatch["uplink_message"]["decoded_payload"]["n_pest"] = 1
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(mismatch)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()
        _, health = get(self.base + "/api/health")
        self.assertTrue(health["ok"])

        bad_time = tts_body("bg-n07", STATE)
        bad_time["uplink_message"]["received_at"] = "not-a-time"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(bad_time)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_17_ttn_redelivery_is_idempotent(self):
        body = tts_body("bg-n08", STATE)
        body["uplink_message"]["received_at"] = srv.utcnow()
        body["uplink_message"]["session_key_id"] = "session-a"
        _, before = get(self.base + "/api/health")
        _, first = self.hook(body)
        _, retry = self.hook(body)
        _, after = get(self.base + "/api/health")
        self.assertFalse(first["duplicate"])
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(after["uplinks"], before["uplinks"] + 1)
        conflict = tts_body("bg-n08", dict(STATE, n_pest=3, action="LOG"))
        conflict["uplink_message"]["received_at"] = body["uplink_message"]["received_at"]
        conflict["uplink_message"]["f_cnt"] = body["uplink_message"]["f_cnt"]
        conflict["uplink_message"]["session_key_id"] = "session-a"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(conflict)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_18_safety_violation_surfaces_as_fault(self):
        unsafe = dict(STATE, soil_safe=False, soil_vwc_pct=72,
                      action="SPRAY")
        self.hook(tts_body("bg-n09", unsafe))
        _, state = get(self.base + "/api/state?node=bg-n09")
        self.assertEqual(state["status"], "fault")
        self.assertTrue(state["safety_violation"])
        self.assertFalse(state["treatment_held"])
        self.assertEqual(state["eil_threshold"], 5)
        self.assertEqual(state["window_minutes"], 30)
        _, logs = get(self.base + "/api/logs?node=bg-n09&n=1")
        self.assertIn("Safety invariant", logs[0]["title"])
        self.assertEqual(logs[0]["sev"], "bad")

    def test_19_csv_export_is_node_scoped_and_oldest_first(self):
        older = tts_body("bg-export", dict(STATE, n_pest=2, action="LOG"))
        older["uplink_message"].update(
            received_at="2026-07-10T01:00:00Z", f_cnt=1)
        newer = tts_body("bg-export", dict(STATE, n_pest=8, action="LOG"))
        newer["uplink_message"].update(
            received_at="2026-07-10T01:05:00Z", f_cnt=2)
        self.hook(newer)
        self.hook(older)
        req = urllib.request.Request(
            self.base + "/api/export.csv?node=bg-export&n=10")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            text = resp.read().decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["device_id"] for row in rows}, {"bg-export"})
        self.assertEqual([row["n_pest"] for row in rows], ["2", "8"])
        self.assertEqual({row["source_kind"] for row in rows}, {"field"})
        self.assertEqual({row["payload_version"] for row in rows}, {"1"})
        self.assertEqual({row["application_id"] for row in rows},
                         {"bananaguard"})

    def test_20_head_obeys_static_allowlist(self):
        req = urllib.request.Request(self.base + "/backend/server.py",
                                     method="HEAD")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(cm.exception.headers["Content-Length"], "0")
        cm.exception.close()
        req = urllib.request.Request(self.base + "/index.html", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_21_oversize_webhook_rejected_without_reading_body(self):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=2)
        conn.putrequest("POST", "/ttn")
        conn.putheader("X-Webhook-Token", TEST_TOKEN)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(srv.MAX_WEBHOOK_BYTES + 1))
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual(resp.status, 413)
        resp.read()
        conn.close()

    def test_22_decoder_rejects_reserved_action_and_bad_ranges(self):
        raw = bytearray(dp.encode_uplink(STATE))
        raw[7] = 0xFF
        with self.assertRaises(dp.PayloadError):
            dp.decode_uplink(bytes(raw))
        with self.assertRaises(dp.PayloadError):
            dp.encode_uplink(dict(STATE, soil_vwc_pct=101))

    def test_23_simulator_route_is_opt_in_and_source_isolated(self):
        body = tts_body("bg-sim-only", dict(STATE, n_pest=2, action="LOG"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.base + "/demo/ttn", body)
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

        status, accepted = self.demo_hook(body)
        self.assertEqual(status, 200)
        self.assertEqual(accepted["source_kind"], "simulator")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.base + "/api/state?node=bg-sim-only")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()
        _, simulated = get(
            self.base + "/api/state?node=bg-sim-only&source=simulator")
        self.assertEqual(simulated["source_kind"], "simulator")
        _, field_nodes = get(self.base + "/api/nodes")
        self.assertNotIn("bg-sim-only",
                         [node["device_id"] for node in field_nodes])

    def test_24_source_filter_is_strict_across_data_endpoints(self):
        for path in ("/api/nodes", "/api/state", "/api/history",
                     "/api/logs", "/api/export.csv"):
            separator = "&" if "?" in path else "?"
            with self.assertRaises(urllib.error.HTTPError, msg=path) as cm:
                get(self.base + path + separator + "source=legacy_unknown")
            self.assertEqual(cm.exception.code, 400, path)
            cm.exception.close()

    def test_25_meta_declares_read_only_capabilities_and_counts(self):
        _, meta = get(self.base + "/api/meta")
        self.assertEqual(meta["api_version"], "1")
        self.assertEqual(meta["backend_profile"], "field")
        self.assertTrue(meta["read_only"])
        self.assertTrue(meta["field_ingest_configured"])
        self.assertFalse(meta["field_ready"])
        self.assertEqual(meta["stale_after_seconds"],
                         srv.STALE_AFTER_SECONDS)
        self.assertEqual(meta["supported_payload_versions"], [1])
        self.assertFalse(meta["capabilities"]["remote_commands"])
        self.assertFalse(meta["capabilities"]["simulator_ingest"])
        self.assertGreater(meta["counts"]["field"], 0)
        self.assertGreater(meta["counts"]["simulator"], 0)
        self.assertGreater(meta["counts"]["nodes"], 0)
        self.assertIn("registered_nodes", meta["counts"])
        datetime.fromisoformat(meta["server_time"])

        _, demo_meta = get(self.demo_base + "/api/meta")
        self.assertEqual(demo_meta["backend_profile"], "demo")
        self.assertTrue(demo_meta["capabilities"]["simulator_ingest"])

    def test_26_excessively_future_source_time_is_rejected(self):
        body = tts_body("bg-future", STATE)
        future = (datetime.now(timezone.utc) +
                  timedelta(seconds=srv.MAX_FUTURE_SKEW_SECONDS + 30))
        body["uplink_message"]["received_at"] = future.isoformat()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(body)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("future", cm.exception.read().decode())
        cm.exception.close()

    def test_27_wet_soil_above_eil_is_a_held_treatment(self):
        held = dict(STATE, n_pest=8, soil_safe=False, soil_vwc_pct=72,
                    action="LOG")
        self.hook(tts_body("bg-held", held))
        _, state = get(self.base + "/api/state?node=bg-held")
        self.assertEqual(state["status"], "held")
        self.assertTrue(state["treatment_held"])
        _, logs = get(self.base + "/api/logs?node=bg-held&n=1")
        self.assertEqual(logs[0]["type"], "held")
        self.assertIn("held", logs[0]["title"].lower())

    def test_28_spray_uplink_is_completed_not_active(self):
        self.hook(tts_body("bg-spray-report", STATE))
        _, state = get(self.base + "/api/state?node=bg-spray-report")
        self.assertEqual(state["status"], "reported")
        self.assertEqual(state["action_status"], "completed")
        self.assertNotEqual(state["status"], "spraying")
        _, logs = get(self.base + "/api/logs?node=bg-spray-report&n=1")
        self.assertIn("reported complete", logs[0]["title"].lower())

    def test_29_node_registry_joins_metadata_and_can_hide_a_node(self):
        self.hook(tts_body("bg-registry", dict(STATE, action="LOG")))
        _, fallback = get(self.base + "/api/state?node=bg-registry")
        self.assertEqual(fallback["display_name"], "bg-registry")
        self.assertFalse(fallback["registered"])

        with srv._db_lock:
            self.conn.execute(
                "INSERT INTO nodes (device_id, display_name, block, latitude, "
                "longitude, enabled) VALUES (?, ?, ?, ?, ?, 1)",
                ("bg-registry", "Packing Shed", "B-2", 1.234, 103.456))
            self.conn.commit()
        _, registered = get(self.base + "/api/state?node=bg-registry")
        self.assertTrue(registered["registered"])
        self.assertEqual(registered["display_name"], "Packing Shed")
        self.assertEqual(registered["block"], "B-2")
        self.assertEqual(registered["latitude"], 1.234)

        with srv._db_lock:
            self.conn.execute(
                "UPDATE nodes SET enabled = 0 WHERE device_id = 'bg-registry'")
            self.conn.commit()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.base + "/api/state?node=bg-registry")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

    def test_30_legacy_database_migration_preserves_and_quarantines_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = temp_dir + "/legacy.db"
            legacy = sqlite3.connect(path)
            legacy.executescript("""
                CREATE TABLE uplinks (
                    id INTEGER PRIMARY KEY, received_at TEXT NOT NULL,
                    device_id TEXT NOT NULL, fport INTEGER, fcnt INTEGER,
                    rssi REAL, snr REAL, sf INTEGER, n_pest INTEGER NOT NULL,
                    soil_safe INTEGER NOT NULL, soil_fault INTEGER NOT NULL,
                    camera_fault INTEGER NOT NULL, infer_ready INTEGER NOT NULL,
                    lockout_active INTEGER NOT NULL, soil_vwc_pct INTEGER,
                    batt_mv INTEGER NOT NULL, action TEXT NOT NULL,
                    sprays_today INTEGER NOT NULL, raw_hex TEXT
                );
                INSERT INTO uplinks VALUES (
                    1, '2026-07-01T00:00:00+00:00', 'bg-legacy', 1, 1,
                    -100, 5, 10, 3, 1, 0, 0, 1, 0, 40, 3900, 'LOG', 0, '00'
                );
            """)
            legacy.commit()
            legacy.close()

            migrated = srv.open_db(path)
            try:
                row = migrated.execute(
                    "SELECT * FROM uplinks WHERE device_id='bg-legacy'"
                ).fetchone()
                self.assertEqual(row["source_kind"], "legacy_unknown")
                self.assertIsNotNone(row["ingested_at"])
                self.assertIsNone(row["payload_version"])
                self.assertIsNone(row["application_id"])
                self.assertEqual(migrated.execute(
                    "PRAGMA user_version").fetchone()[0], srv.SCHEMA_VERSION)
                self.assertEqual(
                    [r[0] for r in migrated.execute(
                        "SELECT version FROM schema_migrations ORDER BY version")],
                    [1, 2, 3, 4])
                self.assertEqual(migrated.execute(
                    "SELECT COUNT(*) FROM uplinks").fetchone()[0], 1)
            finally:
                migrated.close()

    def test_31_loopback_detection_handles_ipv4_mapped_addresses(self):
        self.assertTrue(srv.is_loopback_host("127.0.0.1"))
        self.assertTrue(srv.is_loopback_host("::1"))
        self.assertTrue(srv.is_loopback_host("::ffff:127.0.0.1"))
        self.assertFalse(srv.is_loopback_host("192.0.2.10"))

    def test_32_offline_font_allowlist_is_exact(self):
        expected = {
            "/vendor/fonts/fonts.css",
            "/vendor/fonts/LICENSES.txt",
            "/vendor/fonts/manrope-latin.woff2",
            "/vendor/fonts/material-symbols-rounded.woff2",
            "/vendor/fonts/space-grotesk-latin.woff2",
        }
        self.assertEqual(srv.STATIC_FONT_FILES, expected)
        self.assertEqual(srv.STATIC_LEGAL_FILES,
                         {"/vendor/LICENSES.txt"})
        self.assertEqual(srv.static_route("/vendor/LICENSES.txt"),
                         "/vendor/LICENSES.txt")
        for route in expected:
            self.assertEqual(srv.static_route(route), route)
        for blocked in ("/vendor/fonts/other.woff2",
                        "/vendor/fonts/private.css",
                        "/vendor/fonts/../fonts/other.woff2"):
            self.assertIsNone(srv.static_route(blocked), blocked)

    def test_33_field_ingest_is_disabled_without_server_token(self):
        _, meta = get(self.no_token_base + "/api/meta")
        self.assertFalse(meta["field_ingest_configured"])
        self.assertFalse(meta["field_ready"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.no_token_base + "/ttn",
                 tts_body("bg-no-server-token", STATE))
        self.assertEqual(cm.exception.code, 503)
        self.assertIn("BG_WEBHOOK_TOKEN", cm.exception.read().decode())
        cm.exception.close()

    def test_34_deduplication_is_namespaced_by_provenance(self):
        body = tts_body("bg-source-key", dict(STATE, action="LOG"))
        body["uplink_message"]["session_key_id"] = "shared-session"
        _, field = self.hook(body)
        _, simulated = self.demo_hook(body)
        self.assertFalse(field["duplicate"])
        self.assertFalse(simulated["duplicate"])
        self.assertNotEqual(field["id"], simulated["id"])
        _, field_state = get(
            self.base + "/api/state?node=bg-source-key&source=field")
        _, demo_state = get(
            self.base + "/api/state?node=bg-source-key&source=simulator")
        self.assertEqual(field_state["source_kind"], "field")
        self.assertEqual(demo_state["source_kind"], "simulator")

    def test_35_api_has_no_wildcard_cors(self):
        with urllib.request.urlopen(self.base + "/api/meta") as response:
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_36_field_timestamp_requires_explicit_offset(self):
        missing = tts_body("bg-time-missing", STATE)
        del missing["uplink_message"]["received_at"]
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(missing)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("offset-bearing", cm.exception.read().decode())
        cm.exception.close()

        naive = tts_body("bg-time-naive", STATE)
        naive["uplink_message"]["received_at"] = "2026-07-11T10:00:00"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(naive)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("UTC offset", cm.exception.read().decode())
        cm.exception.close()

        simulator = tts_body("bg-demo-time-fallback", STATE)
        del simulator["uplink_message"]["received_at"]
        status, accepted = self.demo_hook(simulator)
        self.assertEqual(status, 200)
        self.assertEqual(accepted["source_kind"], "simulator")

        for missing_path in ("f_cnt", "application_id"):
            body = tts_body("bg-field-evidence", STATE)
            if missing_path == "f_cnt":
                del body["uplink_message"]["f_cnt"]
            else:
                del body["end_device_ids"]["application_ids"]
            with self.subTest(missing=missing_path), self.assertRaises(
                    urllib.error.HTTPError) as cm:
                self.hook(body)
            self.assertEqual(cm.exception.code, 400)
            self.assertIn(missing_path, cm.exception.read().decode())
            cm.exception.close()

        simulator = tts_body("bg-demo-minimal-evidence", STATE)
        del simulator["uplink_message"]["f_cnt"]
        del simulator["end_device_ids"]["application_ids"]
        status, accepted = self.demo_hook(simulator)
        self.assertEqual(status, 200)
        self.assertEqual(accepted["source_kind"], "simulator")

    def test_37_invalid_utf8_is_a_stable_bad_request(self):
        host, port = self.httpd.server_address
        conn = http.client.HTTPConnection(host, port, timeout=2)
        conn.request("POST", "/ttn", body=b"\xff",
                     headers={"Content-Type": "application/json",
                              "Content-Length": "1",
                               "X-Webhook-Token": TEST_TOKEN})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("error", json.loads(response.read()))
        conn.close()
        _, health = get(self.base + "/api/health")
        self.assertTrue(health["ok"])

    def test_38_node_metadata_update_preserves_disabled_state(self):
        script = os.path.join(os.path.dirname(__file__), "manage_nodes.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            db = os.path.join(temp_dir, "nodes.db")
            common = [sys.executable, script, "--db", db]
            subprocess.run(
                common + ["set", "bg-cli", "--name", "Original",
                          "--disabled"], check=True, capture_output=True,
                text=True)
            subprocess.run(
                common + ["set", "bg-cli", "--name", "Renamed"],
                check=True, capture_output=True, text=True)
            listed = subprocess.run(
                common + ["list"], check=True, capture_output=True,
                text=True)
            rows = json.loads(listed.stdout)
            self.assertEqual(rows[0]["display_name"], "Renamed")
            self.assertFalse(rows[0]["enabled"])

    def test_39_cli_rejects_non_loopback_binding(self):
        script = os.path.join(os.path.dirname(__file__), "server.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, script, "--host", "0.0.0.0", "--port", "0",
                 "--db", os.path.join(temp_dir, "server.db")],
                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-loopback binding is disabled", result.stderr)

        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["BG_WEBHOOK_TOKEN"] = "weak"
            result = subprocess.run(
                [sys.executable, script, "--port", "0", "--db",
                 os.path.join(temp_dir, "server.db")],
                env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("at least 32 UTF-8 bytes", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
