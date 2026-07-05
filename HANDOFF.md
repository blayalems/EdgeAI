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
| Statistical analysis (`analysis/`) | ✅ tested | All five scripts exercised on synthetic data (known outliers caught, TOST detects planted equivalence, battery fit recovers the planted −9 mV/h drain) |

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

### 2026-07-05 — CI workflows: tests, Pages deploy, Windows exe, Android APK

- `.github/workflows/tests.yml`: backend (14), decoder, Eq. 2 (14 +
  scenario invariants) suites plus a py_compile sweep on every push/PR.
- `.github/workflows/pages.yml`: publishes the dashboard (allowlisted
  files only) to GitHub Pages on pushes to main. **One-time setup
  required: repo Settings → Pages → Source = "GitHub Actions".** Pages
  has no backend, so it serves the LIVE·SIM demo.
- `.github/workflows/windows-exe.yml`: PyInstaller onefile build of
  `backend/server.py` with the dashboard bundled (`server.py` gained
  frozen-mode handling: static files from the bundle, DB next to the
  .exe). CI smoke-tests the built exe (health + page + vendor JS over
  HTTP) before uploading the artifact; attaches to Releases on `v*` tags.
- `.github/workflows/android-apk.yml`: generates a Capacitor 7 WebView
  shell around the dashboard and builds a debug-signed, sideload-ready
  APK; artifact on every run, Release asset on `v*` tags.
- **Verify:** YAML parsed clean locally; the exe and APK jobs run on this
  PR (pull_request triggers) — check the Actions tab for green runs. The
  Pages job needs the one-time settings switch before its first deploy.
- **Next:** none — watch the PR checks.

### 2026-07-05 — Post-merge fixes from the PR #2 code review (13 findings)

- **backend/server.py**: static serving now uses a strict allowlist
  (`index.html`, `support.js`, `Ring.dc.html`, `vendor/*.js`) — the repo
  tree, including the SQLite DB and firmware sources, is no longer
  downloadable from a public host (was P1). Partial `decoded_payload`
  from a TTN formatter falls back to raw decoding instead of crashing on
  NOT NULL columns; `?n=` limits are clamped to ≥1; "latest" state is
  ordered by `received_at` (id tie-break) so a TTN redelivery of an old
  frame can't regress the dashboard. Also: PyInstaller-frozen mode
  support (bundle dir for static files, DB next to the .exe).
- **ml/evaluate.py**: INT8 input quantization no longer divides by 255 —
  the converter calibrates on 0–255 pixels, so the correct mapping is
  `pixel/scale + zp` (was P1: reported INT8 metrics were of a washed-out
  input distribution).
- **analysis/**: paired TOST drops rows with a missing value in either
  column (pairing preserved); impact window uses the full log span, not
  first-spray→last-spray; battery night window applies a `--tz-offset`
  (default UTC+8 Davao) before selecting solar-free hours.
- **index.html**: polling starts on an empty backend and flips to live on
  the first uplink (no reload needed); display IDs are assigned per
  device_id first-seen and never reshuffled; the soil gate shows the
  firmware's own `soil_safe` band decision instead of re-deriving it
  one-sided; sensor faults render as a distinct red FAULT state instead
  of masquerading as a wet-soil HELD.
- **Verify:** `python3 backend/test_backend.py` → 14 tests OK (4 new
  ones cover the backend fixes); headless-Chromium check confirms
  SIM→TTN flip after the first uplink on a fresh DB.

### 2026-07-05 — Statistical analysis (`analysis/`)

- `figstyle.py`: shared manuscript matplotlib defaults (serif, 3.5″
  single-column, 300 dpi, headless Agg).
- `mad_filter.py`: 3×MAD robust outlier rule (`mad_mask()` + CSV CLI),
  with a degenerate-MAD fallback.
- `tost.py`: TOST equivalence (Welch independent + paired) implemented
  directly on scipy — p_TOST, 90 % CI, plain-English verdict.
- `detection_metrics.py`: presence/absence confusion matrix, P/R/F1
  (+ optional LaTeX table), count-agreement MAE, two figures; consumes
  the ground-truth CSV from `test/ground_truth_logger.py`.
- `battery_autonomy.py`: 3×MAD-cleans batt_mv from the backend DB or a
  CSV, fits each solar-free night separately, averages the slopes →
  mV/h, estimated draw, zero-solar autonomy, PASS/FAIL vs the ≥7-day
  requirement, shaded trace figure.
- `impact.py`: targeted sprays vs calendar baseline → liters avoided,
  % pesticide cut, kg CO₂e saved; every assumption is a documented CLI
  flag; comparison figure.
- **Verify:** all scripts run against synthetic data with known ground
  truth — the MAD filter dropped exactly the planted outliers, TOST
  declared the planted-equivalent samples equivalent (p=0.0018), and the
  per-night battery fit recovered the planted −9 mV/h drain.
- **Next:** push branch, open PR. Post-merge follow-ups tracked under
  "Open risks" and per-folder READMEs.

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
