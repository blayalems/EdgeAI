# BananaGuard — Edge-AI Pest Sentinel

Solar-powered ESP32 field nodes detect banana pests on-device (INT8
MobileNetV2, TFLite-Micro), gate spraying on soil state (Eq. 2:
`spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe`), uplink 9-byte LoRaWAN telemetry
through The Things Network, and feed a local telemetry backend and web
dashboard. The Sheets helper is retained only as a legacy prototype path.

## Repository map

| Folder | What lives here | Owner / timeline |
|---|---|---|
| `firmware/` | ESP-IDF node firmware: camera, ROI diff, inference, decision engine, actuation, LoRaWAN, deep sleep | Member 2, Weeks 4–13 |
| `ml/` | Training pipeline: frozen split → augmented MobileNetV2 transfer learning → per-class P/R/F1 → INT8 quantization → C-array export → latency benchmarks | Member 2/3, Weeks 9–12 |
| `decoder/` | TTN v3 JavaScript payload formatter for the 9-byte uplink + node tests | — |
| `backend/` | Zero-dependency Python listener: TTN webhook → SQLite → dashboard JSON API + static serving, plus a no-hardware uplink simulator | — |
| `cloud/` | Legacy, non-evidentiary TTN → Google Sheets prototype; not an Actual-mode or validation data source | — |
| `test/` | Servo specimen rig (Arduino), host-side decision-engine mirror + Eq. 2 unit tests & scenario sim, Phase-1 ground-truth logger | Weeks 13–15 |
| `analysis/` | 3×MAD filter, TOST equivalence, confusion/F1 tables, battery autonomy, pesticide/CO₂ impact — manuscript-format figures | Member 3, Weeks 16 & 28 |
| `docs/` | Supplied IEEE LaTeX manuscript plus implementation traceability, unresolved hardware gates, and study-design findings | Project team |
| `index.html`, `support.js`, `Ring.dc.html`, `vendor/` | Web dashboard (single-page, no build step; React vendored for offline use). Distinguishes TTN telemetry, stale/link-lost state, and synthetic demo data | — |
| `tools/` | Deterministic standalone builder and exact source-parity checker | — |

## Data flow

```
node firmware ──9-byte uplink──▶ TTN ──webhook──▶ backend/server.py ──▶ SQLite
     ▲                            │                     │                  │
 ml/ export_c_array.py            └──▶ cloud/ Sheets    ├──▶ dashboard     └──▶ analysis/
     (trained INT8 model)              (legacy only)    └──▶ /api/* (field-provenanced data)
```

## CI / builds (GitHub Actions)

| Workflow | Trigger | Output |
|---|---|---|
| `tests.yml` | every push & PR | backend + decoder + Eq. 2 suites, py_compile sweep |
| `standalone.yml` | dashboard PRs, `main`, and `v*` tags | source-identifiable, fully offline `BananaGuard-Standalone.html`; responsive/mode/offline browser smoke; attached to Releases on tags |
| `pages.yml` | push to `main` | dashboard on GitHub Pages (explicit Demo mode) plus the downloadable standalone. One-time setup: **Settings → Pages → Source: GitHub Actions** |
| `windows-exe.yml` | push to `main`, `v*` tags, PRs touching backend/dashboard | `BananaGuard.exe` — server + dashboard in one file; CI smoke-tests the API, page and vendor JS over HTTP; attached to Releases on tags |
| `android-apk.yml` | push to `main`, `v*` tags, PRs touching the dashboard | debug-signed, Demo-only `BananaGuard-debug.apk` (the wrapper has no authenticated HTTPS backend path yet); attached to Releases on tags |

Tag a release (`git tag v0.1.0 && git push --tags`) to attach the standalone
HTML, Windows executable, and debug-signed Demo-only APK to a GitHub Release.

## Tests

```sh
python backend/test_backend.py                         # backend (39 tests)
node decoder/test_decoder.js                           # payload decoder
( cd test/decision_sim && python -m unittest discover -p "test*.py" -v && python scenario_sim.py )
( cd ml && python -m unittest discover -s tests -v )   # frozen-split/model contracts
( cd analysis && python -m unittest discover -s tests -v )
python -m unittest discover -s test -p test_standalone_tools.py -v

# Deterministic offline dashboard artifact
npm ci
python tools/build_standalone.py
python tools/check_standalone.py
npx playwright install chromium   # first browser-smoke run only
npm run test:browser
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
- **Dashboard Demo** — no hardware or backend needed. Open `index.html`
  directly, or serve the repository with any static HTTP server. Demo values
  are generated only in the page and make zero `/api/*` requests.

- **Backend provenance exercise** — the simulator writes explicitly tagged
  `simulator` records so the ingest/API contract can be tested without
  hardware:

  ```sh
  python3 backend/server.py --allow-simulator &      # port 8000
  python3 backend/simulate_uplinks.py --interval 5   # explicit simulator-source uplinks
  # inspect records; neither dashboard mode displays simulator rows
  curl "http://localhost:8000/api/nodes?source=simulator"
  ```

  The dashboard starts in **Demo** with clearly labelled synthetic data.
  **Actual** is read-only and displays only records provenanced as `field` by
  the local backend. Opening `index.html` directly (`file://`), GitHub Pages,
  or the current Android wrapper remains Demo-only; choosing Actual there
  shows **Setup required** and never falls back to synthetic telemetry.

## Field-readiness boundary

The repository is host-tested, but it is not yet a field-ready ESP32-C6
release. The reviewed firmware pin map targets classic ESP32/WROOM-32 and now
fails closed on other targets; the manuscript's C6 board map, OV5640/OV5642
choice, peak-RAM budget, Philippine LoRaWAN plan, trained INT8 model, OTAA
credentials, and sensor/EIL calibration remain deployment gates. See
`docs/implementation_audit.md` before purchasing or flashing hardware.

## Project conventions

- Every commit updates `README.md` and `HANDOFF.md` (status + what changed).
- Every tunable constant in firmware lives in `firmware/main/app_config.h`
  and only there. Scripts mirror those constants explicitly and say so.
- The LoRaWAN uplink payload spec (9 bytes, v1) in
  `firmware/main/lora_telemetry.h` is the single contract between firmware,
  decoder, backend and analysis. Change it in one place, bump the version
  byte, update all consumers in the same commit.
- The backend serves only the allowlisted dashboard files over HTTP; API
  "latest" state is ordered by uplink time (normalized to fixed-precision
  UTC on insert) so TTN redeliveries and mixed timestamp formats cannot
  regress it.

## License

Application code is MIT — see `LICENSE`. React/ReactDOM/Modernizr notices and
their MIT text are in `vendor/LICENSES.txt`. Manrope, Space Grotesk, and
Material Symbols notices and complete OFL 1.1 / Apache 2.0 texts are in
`vendor/fonts/LICENSES.txt`. Every packaged dashboard distribution includes
all three license files; the standalone embeds human-readable links and exact
copies in its source manifest. The Android wrapper additionally embeds its
pinned Capacitor MIT notice and exact Gradle runtime inventory. The Windows
distribution ships the exact CPython and PyInstaller terms plus a frozen build
inventory beside the executable; see `appshell/` and `packaging/windows/`.
