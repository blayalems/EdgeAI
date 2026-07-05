# BananaGuard — Edge-AI Pest Sentinel

Solar-powered ESP32 field nodes detect banana pests on-device (INT8
MobileNetV2, TFLite-Micro), gate spraying on soil state (Eq. 2:
`spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe`), uplink 9-byte LoRaWAN telemetry
through The Things Network, and feed a web dashboard plus cloud log.

## Repository map

| Folder | What lives here | Owner / timeline |
|---|---|---|
| `firmware/` | ESP-IDF node firmware: camera, ROI diff, inference, decision engine, actuation, LoRaWAN, deep sleep | Member 2, Weeks 4–13 |
| `ml/` | Training pipeline: frozen split → augmented MobileNetV2 transfer learning → per-class P/R/F1 → INT8 quantization → C-array export → latency benchmarks | Member 2/3, Weeks 9–12 |
| `decoder/` | TTN v3 JavaScript payload formatter for the 9-byte uplink + node tests | — |
| `backend/` | Zero-dependency Python listener: TTN webhook → SQLite → dashboard JSON API + static serving, plus a no-hardware uplink simulator | — |
| `cloud/` | Lowest-code cloud log: TTN webhook → Google Sheets Apps Script (Grafana notes included) | — |
| `test/` | Servo specimen rig (Arduino), host-side decision-engine mirror + Eq. 2 unit tests & scenario sim, Phase-1 ground-truth logger | Weeks 13–15 |
| `analysis/` | 3×MAD filter, TOST equivalence, confusion/F1 tables, battery autonomy, pesticide/CO₂ impact — manuscript-format figures | Member 3, Weeks 16 & 28 |
| `index.html`, `support.js`, `Ring.dc.html`, `vendor/` | Web dashboard (single-page, no build step; React vendored for offline use). Auto-detects the backend: shows `LIVE · TTN` on real uplinks, `LIVE · SIM` standalone | — |

## Data flow

```
node firmware ──9-byte uplink──▶ TTN ──webhook──▶ backend/server.py ──▶ SQLite
     ▲                            │                     │                  │
 ml/ export_c_array.py            └──▶ cloud/ Sheets    ├──▶ dashboard     └──▶ analysis/
     (trained INT8 model)              (Apps Script)    └──▶ /api/*  (live data)
```

## CI / builds (GitHub Actions)

| Workflow | Trigger | Output |
|---|---|---|
| `tests.yml` | every push & PR | backend + decoder + Eq. 2 suites, py_compile sweep |
| `pages.yml` | push to `main` | dashboard on GitHub Pages (demo/SIM mode). One-time setup: **Settings → Pages → Source: GitHub Actions** |
| `windows-exe.yml` | push to `main`, `v*` tags, PRs touching backend/dashboard | `BananaGuard.exe` — server + dashboard in one file, smoke-tested in CI; attached to Releases on tags |
| `android-apk.yml` | push to `main`, `v*` tags, PRs touching the dashboard | debug-signed `BananaGuard-debug.apk` (Capacitor WebView shell, sideload-ready); attached to Releases on tags |

Tag a release (`git tag v0.1.0 && git push --tags`) to get the .exe and
.apk attached to a GitHub Release automatically.

## Tests

```sh
python3 backend/test_backend.py                        # backend (10 tests)
node decoder/test_decoder.js                           # payload decoder
( cd test/decision_sim && python3 test_decision_engine.py && python3 scenario_sim.py )
```

## Quick start

```sh
git clone https://github.com/blayalems/EdgeAI.git
cd EdgeAI
```

- **Firmware** — see `firmware/README.md` (ESP-IDF ≥ 5.1).
- **ML pipeline** — see `ml/README.md`; end-to-end is
  `split_dataset.py → train_mobilenetv2.py → evaluate.py →
  quantize_int8.py → export_c_array.py` (the last step writes
  `firmware/main/model_data.cc`).
- **Dashboard + backend** — no hardware needed:

  ```sh
  python3 backend/server.py &                        # port 8000
  python3 backend/simulate_uplinks.py --interval 5   # fake TTN uplinks
  # open http://localhost:8000
  ```

  Opening `index.html` directly (file://) still works and runs on
  simulated in-page data.

## Project conventions

- Every commit updates `README.md` and `HANDOFF.md` (status + what changed).
- Every tunable constant in firmware lives in `firmware/main/app_config.h`
  and only there. Scripts mirror those constants explicitly and say so.
- The LoRaWAN uplink payload spec (9 bytes, v1) in
  `firmware/main/lora_telemetry.h` is the single contract between firmware,
  decoder, backend and analysis. Change it in one place, bump the version
  byte, update all consumers in the same commit.
- The backend serves only the allowlisted dashboard files over HTTP; API
  "latest" state is ordered by uplink time so TTN redeliveries cannot
  regress it.

## License

MIT — see `LICENSE`.
