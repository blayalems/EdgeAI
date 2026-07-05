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
| `index.html`, `support.js`, `Ring.dc.html` | Web dashboard (single-page, no build step) | — |

More folders (`test/`, `analysis/`)
land in subsequent commits — see `HANDOFF.md` for live status.

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

## License

MIT — see `LICENSE`.
