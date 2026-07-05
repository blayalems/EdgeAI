# HANDOFF — living status document

Updated on **every commit**. Newest entry first. Each entry: what changed,
how to verify it, what is still open.

## Component status

| Component | Status | Notes |
|---|---|---|
| Firmware (`firmware/`) | ✅ code-complete | Needs real model in `model_data.cc` + ttn-esp32 component + OTAA keys before field use |
| Dashboard (`index.html`) | ✅ live-wired | Polls `/api/*` when served by the backend (verified in headless Chromium); falls back to in-page sim on `file://` or without backend |
| ML pipeline (`ml/`) | ✅ code-complete | Needs the real image dataset; TF scripts compile-checked, stdlib scripts (split/export/latency-parse) exercised end-to-end |
| TTN decoder (`decoder/`) | ✅ tested | `node decoder/test_decoder.js` passes; paste into TTN console when the application exists |
| Backend + cloud log (`backend/`, `cloud/`) | ✅ tested | 10/10 integration tests pass; simulator exercises the full webhook→DB→API chain; Apps Script needs a live deploy to verify |
| Test & validation (`test/`) | ✅ tested | 14/14 Eq. 2 unit tests + scenario invariants pass on host; servo rig needs bench bring-up with a real servo |
| Statistical analysis (`analysis/`) | ⬜ pending | |

## Open risks (carry-over from planning)

1. **TFLite-Micro latency on ESP32-C6** — the cited ~240 ms benchmark was
   measured on a dual-core Xtensa ESP32, not the single-core RISC-V C6.
   Retire this risk in Week 4 with `ml/benchmark_latency.py` (host) and the
   on-device timing log parser before committing to the C6.
2. **ESP-IDF vs Arduino** — belongs in the Week 2 trade-off matrix. Current
   firmware is ESP-IDF; the servo test rig is Arduino (isolated, no shared
   code, so the choice stays open for the node itself).
3. **Edge Impulse alternative** — Edge Impulse Studio can replace most of
   `ml/`; decide in the Week 2 trade-off doc. The `ml/` scripts are the
   full-control path.

---

## Log

### 2026-07-05 — Test & validation code (`test/`)

- `decision_sim/decision_engine.py`: line-for-line Python mirror of
  `decision_engine.c` with the app_config constants; the executable spec
  of Eq. 2.
- `decision_sim/test_decision_engine.py`: 14 tests — strict `>` EIL
  boundary, fault priority (camera > soil), all three lockouts with exact
  boundary values, never-sprayed sentinel, plus a 3 584-combination sweep
  asserting the two safety invariants (SPRAY ⟹ everything OK; fault ⟹
  never SPRAY).
- `decision_sim/scenario_sim.py`: multi-day traces with firmware-style
  carried state (sprays_today, minutes-since-spray) — dry outbreak hits
  the daily cap, wet week never sprays, mid-outbreak camera fault goes
  FAULT, brown-out goes LOCKOUT; hard asserts at the end, `--csv` for
  analysis.
- `servo_rig/servo_rig.ino`: Arduino servo carousel, 6 known densities,
  Fisher-Yates randomized trials, `RIG,ms,trial,pos,density` CSV over
  serial.
- `ground_truth_logger.py`: rig serial/captured-file/manual entry →
  Phase-1 ground-truth CSV consumed by the analysis stage.
- **Verify:** both Python harnesses pass (`test/decision_sim/`); rig
  firmware compiles by inspection only (no Arduino toolchain here).
- **Next:** statistical analysis (`analysis/`).

### 2026-07-05 — Dashboard wired to the backend (live data)

- `index.html`: on load, probes `/api/health`; if the backend answers with
  stored uplinks it flips to live mode — polls `/api/nodes`, `/api/logs`,
  `/api/history` every 5 s, maps device rows onto the fleet/node views,
  and stops the random-walk simulation for every telemetry-backed field
  (pest window, soil VWC, battery, spray status, sprays today, RSSI/SNR/SF,
  history sparklines). Fields the 9-byte uplink does not carry (air temp,
  humidity, solar model) keep their cosmetic simulation. Top-bar badge now
  reads `LIVE · TTN` (green) vs `LIVE · SIM` (red).
- `vendor/`: React 18.3.1 UMD builds committed locally and loaded before
  `support.js` (which skips its CDN fetch when `window.React` exists) —
  the dashboard now works fully offline; file hashes verified against the
  SRI pins inside `support.js`.
- **Verify:** `python3 backend/server.py` + `simulate_uplinks.py --once`,
  open `http://localhost:8000` → badge shows LIVE · TTN and node cards
  show the simulator's values. Confirmed in headless Chromium.
- **Next:** test & validation code (`test/`).

### 2026-07-05 — Backend (webhook → SQLite → API) + Sheets cloud log

- `backend/server.py`: single stdlib file. `POST /ttn` accepts TTS v3
  webhooks (prefers `decoded_payload`, falls back to decoding raw
  `frm_payload` via `decode_payload.py` — the Python twin of the JS
  decoder), optional `X-Webhook-Token` auth, SQLite storage, JSON API
  (`/api/health|nodes|state|history|logs`) and static serving of the
  dashboard from the repo root.
- `backend/simulate_uplinks.py`: posts realistic raw-payload webhook
  bodies for 3 fake nodes — full-chain demo with zero hardware.
- `backend/test_backend.py`: 10 integration tests (auth, raw + decoded +
  fault payloads, bad-payload rejection, derived state, per-device
  latest, history ordering, logs, health, encode/decode round-trip).
- `cloud/ttn_webhook_to_sheets.gs`: Apps Script web app appending one
  sheet row per uplink, with its own raw-payload decoder and optional
  `?token=` check; setup steps in the file header. Grafana-over-SQLite
  note in `cloud/README.md`.
- **Verify:** `python3 backend/test_backend.py` → OK (10 tests); smoke:
  server + `simulate_uplinks.py --once` + `curl /api/nodes` exercised.
- **Next:** wire the dashboard to `/api/*` (falls back to in-page sim).

### 2026-07-05 — TTN payload decoder (`decoder/`)

- `ttn_payload_decoder.js`: TTS v3 `decodeUplink()` for the 9-byte v1
  payload — big-endian uint16s, flag bits, `0xFF` VWC sentinel → `null`,
  action code → name; rejects wrong length / unknown version with
  `errors` instead of garbage data.
- `test_decoder.js`: nominal, uint16 boundary, fault, lockout and error
  vectors. **Verify:** `node decoder/test_decoder.js` → "all tests passed".
- **Next:** backend (TTN webhook → SQLite → dashboard API) + Sheets
  alternative in `cloud/`.

### 2026-07-05 — ML training pipeline (`ml/`) + on-device timing hook

- `split_dataset.py`: hash-of-path deterministic split; test set frozen in
  `splits/test_manifest.json` before any training — files can never leave
  the test set, and a missing frozen file is a hard error.
- `train_mobilenetv2.py`: Keras augmentation (flip/rotate/zoom/translate/
  brightness/contrast), MobileNetV2 alpha=0.35 @ 96×96 transfer learning
  in two phases (frozen head, then top-40-layer fine-tune at LR/10), class
  weights, early stopping.
- `evaluate.py`: per-class precision/recall/F1 + confusion matrix on the
  frozen test set, for BOTH the float .keras and the INT8 .tflite (same
  code path ⇒ honest quantization-loss numbers), plus the firmware
  operating point (weevil @ ≥60 % confidence).
- `quantize_int8.py`: full-INT8 PTQ, int8 in/out, representative data from
  the train split only; asserts int8 I/O and warns near the flash budget.
- `export_c_array.py`: .tflite → `firmware/main/model_data.cc`
  (`alignas(16)`, `g_model_data[_len]`); generated file syntax-checked
  against the real `model_data.h`.
- `benchmark_latency.py` (host sanity) + `device_latency.py` (parses
  `invoke_us=` serial lines, PASS/FAIL vs budget). **`inference.cc` now
  logs `invoke_us=<n>` per classification** — this is the Week-4 C6-risk
  instrument.
- **Verify:** `cd ml && python3 -m py_compile *.py`; split determinism and
  the exporter were exercised with synthetic data (TensorFlow itself not
  installable in this session — train/quantize scripts are compile-checked
  only, run them where TF ≥ 2.15 is available).
- **Next:** TTN payload decoder (`decoder/`).

### 2026-07-05 — Repo hygiene: README, HANDOFF, LICENSE, .gitignore

- Added top-level `README.md` (repo map, conventions), this `HANDOFF.md`,
  MIT `LICENSE`, and a root `.gitignore` (Python, ML artifacts, SQLite DBs,
  analysis outputs).
- Established the two repo rules: (a) every commit updates README+HANDOFF,
  (b) the 9-byte uplink payload in `firmware/main/lora_telemetry.h` is the
  single cross-component contract.
- **Verify:** `git log --stat` shows this commit touches only docs/meta files.
- **Next:** ML training pipeline in `ml/`.
