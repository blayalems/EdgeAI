#!/usr/bin/env python3
"""Post realistic TTS v3 webhook bodies to a running backend - lets the
whole chain (webhook -> decode -> SQLite -> API -> dashboard) be exercised
without a single piece of hardware or a TTN account.

    python3 backend/server.py &
    python3 backend/simulate_uplinks.py --once        # one uplink per node
    python3 backend/simulate_uplinks.py --interval 5  # continuous

Sends raw frm_payload only (no decoded_payload), so it also covers the
server-side Python decoder path.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import time
import urllib.request
from datetime import datetime, timezone

from decode_payload import encode_uplink

NODES = [
    ("bg-n01", "North Ridge"),
    ("bg-n02", "Creekside"),
    ("bg-n03", "Highland"),
]


def fake_state(rng: random.Random, dev: str) -> dict:
    n_pest = max(0, int(rng.gauss(3, 2.5)))
    vwc = None if rng.random() < 0.03 else int(rng.uniform(15, 75))
    # Mirror the current manuscript/firmware contract: saturation is the
    # environmental inhibitor; plausible readings at or below 60% are safe.
    soil_safe = vwc is not None and vwc <= 60
    batt = int(rng.uniform(3450, 4150))
    if vwc is None:
        action = "FAULT"
    elif n_pest > 5 and soil_safe:
        action = "SPRAY" if rng.random() < 0.7 else "LOCKOUT"
    else:
        action = "LOG"
    return {
        "n_pest": n_pest, "soil_safe": soil_safe, "soil_fault": vwc is None,
        "camera_fault": False, "infer_ready": True,
        "lockout_active": action == "LOCKOUT", "soil_vwc_pct": vwc,
        "batt_mv": batt, "action": action,
        "sprays_today": rng.randint(0, 4),
    }


def webhook_body(dev_id: str, state: dict, fcnt: int,
                 rng: random.Random) -> dict:
    return {
        "end_device_ids": {"device_id": dev_id,
                           "application_ids": {"application_id": "bananaguard"}},
        "uplink_message": {
            "f_port": 1, "f_cnt": fcnt,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "session_key_id": "sim-session",
            "frm_payload": base64.b64encode(encode_uplink(state)).decode(),
            "rx_metadata": [{"gateway_ids": {"gateway_id": "sim-gw"},
                             "rssi": rng.randint(-110, -85),
                             "snr": round(rng.uniform(3, 11), 1)}],
            "settings": {"data_rate": {"lora": {"spreading_factor": 10,
                                                "bandwidth": 125000}}},
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8000/ttn")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--token", default=os.environ.get("BG_WEBHOOK_TOKEN"),
                    help="X-Webhook-Token (defaults to BG_WEBHOOK_TOKEN)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    fcnt = 0
    while True:
        fcnt += 1
        for dev_id, name in NODES:
            body = webhook_body(dev_id, fake_state(rng, dev_id), fcnt, rng)
            headers = {"Content-Type": "application/json"}
            if args.token:
                headers["X-Webhook-Token"] = args.token
            req = urllib.request.Request(
                args.url, json.dumps(body).encode(), headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                out = json.loads(resp.read())
            print(f"{dev_id} ({name}): {out}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
