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
| `index.html`, `support.js`, `Ring.dc.html` | Web dashboard (single-page, no build step) | — |

More folders (`backend/`, `decoder/`, `cloud/`, `test/`, `analysis/`)
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
- **Dashboard** — open `index.html` in a browser (runs on simulated data
  until the backend is up).

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
