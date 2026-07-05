# HANDOFF — living status document

Updated on **every commit**. Newest entry first. Each entry: what changed,
how to verify it, what is still open.

## Component status

| Component | Status | Notes |
|---|---|---|
| Firmware (`firmware/`) | ✅ code-complete | Needs real model in `model_data.cc` + ttn-esp32 component + OTAA keys before field use |
| Dashboard (`index.html`) | ✅ demo mode | Simulated data; live-data wiring pending |
| ML pipeline (`ml/`) | ⬜ pending | |
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

### 2026-07-05 — Repo hygiene: README, HANDOFF, LICENSE, .gitignore

- Added top-level `README.md` (repo map, conventions), this `HANDOFF.md`,
  MIT `LICENSE`, and a root `.gitignore` (Python, ML artifacts, SQLite DBs,
  analysis outputs).
- Established the two repo rules: (a) every commit updates README+HANDOFF,
  (b) the 9-byte uplink payload in `firmware/main/lora_telemetry.h` is the
  single cross-component contract.
- **Verify:** `git log --stat` shows this commit touches only docs/meta files.
- **Next:** ML training pipeline in `ml/`.
