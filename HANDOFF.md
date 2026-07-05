# HANDOFF — living status document

Updated on **every commit**. Newest entry first. Each entry: what changed,
how to verify it, what is still open.

## Component status

| Component | Status | Notes |
|---|---|---|
| Firmware (`firmware/`) | ✅ code-complete | Needs real model in `model_data.cc` + ttn-esp32 component + OTAA keys before field use |
| Dashboard (`index.html`) | ✅ demo mode | Simulated data; live-data wiring pending |
| ML pipeline (`ml/`) | ✅ code-complete | Needs the real image dataset; TF scripts compile-checked, stdlib scripts (split/export/latency-parse) exercised end-to-end |
| TTN decoder (`decoder/`) | ⬜ pending | |
| Backend + cloud log (`backend/`, `cloud/`) | ⬜ pending | |
| Test & validation (`test/`) | ⬜ pending | |
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
