#!/usr/bin/env python3
"""Backend integration tests — stdlib unittest, in-process server on an
ephemeral port, temp database. Run: python3 backend/test_backend.py
"""
import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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


def tts_body(dev, state, decoded=False):
    up = {
        "f_port": 1, "f_cnt": 7,
        "frm_payload": base64.b64encode(dp.encode_uplink(state)).decode(),
        "rx_metadata": [{"rssi": -97, "snr": 8.2}],
        "settings": {"data_rate": {"lora": {"spreading_factor": 10}}},
    }
    if decoded:
        up["decoded_payload"] = dict(state, version=1)
    return {"end_device_ids": {"device_id": dev}, "uplink_message": up}


STATE = {"n_pest": 7, "soil_safe": True, "soil_fault": False,
         "camera_fault": False, "infer_ready": True, "lockout_active": False,
         "soil_vwc_pct": 38, "batt_mv": 3921, "action": "SPRAY",
         "sprays_today": 2}


class BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = srv.open_db(":memory:")
        cls.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), srv.make_handler(cls.conn, token="s3cret"))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def hook(self, body):
        return post(self.base + "/ttn", body,
                    {"X-Webhook-Token": "s3cret"})

    def test_01_auth_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.base + "/ttn", tts_body("bg-n01", STATE))
        self.assertEqual(cm.exception.code, 403)

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

    def test_05_bad_payload_rejected(self):
        body = tts_body("bg-n01", STATE)
        body["uplink_message"]["frm_payload"] = base64.b64encode(
            b"\x02\x00\x00").decode()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.hook(body)
        self.assertEqual(cm.exception.code, 400)

    def test_06_state_and_derivations(self):
        _, s = get(self.base + "/api/state?node=bg-n01")
        self.assertEqual(s["n_pest"], 7)
        self.assertEqual(s["status"], "spraying")
        self.assertEqual(s["batt_pct"], 69.0)  # (3921-3300)/900
        self.assertEqual(s["sf"], 10)
        _, s3 = get(self.base + "/api/state?node=bg-n03")
        self.assertEqual(s3["status"], "fault")
        self.assertIsNone(s3["soil_vwc_pct"])

    def test_07_nodes_latest_per_device(self):
        _, nodes = get(self.base + "/api/nodes")
        self.assertEqual([n["device_id"] for n in nodes],
                         ["bg-n01", "bg-n02", "bg-n03"])

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

    def test_10_decoder_roundtrip(self):
        self.assertEqual(dp.decode_uplink(dp.encode_uplink(STATE)),
                         dict(STATE, version=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
