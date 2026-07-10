# BananaGuard Node Firmware (ESP32 / ESP-IDF)

Firmware for the BananaGuard edge node: camera-based three-species detection
with multi-ROI frame differencing, INT8 MobileNetV2 inference (TFLite-Micro), soil
gating, solenoid actuation with hard safety lockout, LoRaWAN telemetry and a
30-minute deep-sleep duty cycle.

**Member 2 deliverable, Weeks 4–13. Current reviewed target: ESP-IDF ≥ 5.1,
classic ESP32 DevKit/WROOM-32 pin map.** `app_config.h` deliberately fails a
build for ESP32-C6 until a board-specific C6 pin/peripheral map is reviewed;
GPIO33–35 and the present SPI allocation cannot be carried over unchanged.

## Module map

| Module | Files | Notes |
|---|---|---|
| Camera capture | `camera_capture.[ch]` | ArduCAM Mini 5MP Plus (OV5642 + ArduChip): SPI FIFO capture at 320×240 RGB565, box-downscale to model input |
| Frame-diff ROI | `roi_diff.[ch]` | 64×48 grayscale reference in RTC RAM; disconnected components → up to 12 padded crops; skips inference when nothing moved |
| Inference | `inference.h/.cc`, `model_data.h/.cc` | TFLite-Micro, 4-class INT8 MobileNetV2 as C array; rejects class 0/low confidence and counts all three target species |
| Detection aggregator | `detection_agg.[ch]` | Rolling 30-min N̂_pest counter in RTC memory — bucketed, magic+CRC guarded; spec in the header |
| Soil sensing | `soil_sensor.[ch]` | ADC (default) or I2C probe, Week-5 quadratic calibration, binary `Soil_safe`, plausibility fault detection |
| Decision engine | `decision_engine.[ch]` | Eq. 2 exactly: spray ⟺ (N̂_pest > N_EIL≈5) ∧ Soil_safe; pure function, host-testable |
| Actuation | `actuation.[ch]` | Relay GPIO, timed solenoid pulse, hard lockout (max 4/day + 30-min gap, RTC-persisted), refuses on sensor fault |
| LoRaWAN telemetry | `lora_telemetry.[ch]` | OTAA, 9-byte binary uplink; RF is blocked until the deployment frequency plan is explicitly verified |
| Power manager | `power_mgr.[ch]` | Deep-sleep scheduler, 30-min cycle with 45-min jumper switch (Table III contingency), battery mV sampling |
| Event log + watchdog | `event_log.[ch]` | SPIFFS CSV of every decision, reset-reason forensics, task WDT, brownout recovery |
| **Config** | **`app_config.h`** | **every threshold, pin and coefficient — the only file you tune** |
| Cycle orchestration | `main.c` | one wake = one pass of the whole pipeline |

## Build & flash

```sh
cd firmware
idf.py set-target esp32
idf.py build            # fetches esp-tflite-micro from the component registry
idf.py -p /dev/ttyUSB0 flash monitor
```

## Two things to drop in before field deployment

1. **Model** — `model_data.cc` is a placeholder. After training freeze:
   `xxd -i pest_mnv2_int8.tflite`, paste into `model_data.cc`. Until then the
   firmware runs the full cycle but logs inference as not-ready (never sprays).
2. **LoRaWAN stack** — clone [ttn-esp32](https://github.com/manuelbl/ttn-esp32)
   into `firmware/components/`; `lora_telemetry.c` detects it automatically
   (`__has_include("ttn.h")`) and otherwise compiles a stub that logs the exact
   payload bytes. Set the OTAA keys in `app_config.h`, confirm the gateway and
   locally authorized regional channel plan, then set
   `BG_LORA_PLAN_VERIFIED=1`.

The 300 KB tensor arena and 320×240 RGB565 frame coexist during inference
(roughly 454 KB before stack, ROI scratch, drivers and model metadata). A
classic WROOM-32 without PSRAM cannot satisfy that peak allocation. Treat a
PSRAM-capable board or a measured arena/frame reduction as a hardware gate,
not as an optional optimization.

Also validate the trimmed OV5642 register table in `camera_capture.c` against
ArduCAM's reference `ov5642_regs.h` during Week-4 bring-up.

## Safety model (spray path)

Three independent layers must all agree before the solenoid energizes:

1. `decision_evaluate()` — Eq. 2 plus fault/lockout/battery inhibitors,
2. `actuation_spray()` — re-checks soil safety, battery, daily limit,
   minimum gap and fault flags internally (does not trust the caller),
3. hardware default — relay pin driven OFF at boot, after every pulse, and
   held OFF through deep sleep (`gpio_hold_en`).

Sensor faults (camera probe failure, soil reading outside plausibility
limits) always resolve to *no spray* and are reported in the uplink flags.

## RTC persistence inventory (survives deep sleep, not power loss)

| Owner | Data | Guard |
|---|---|---|
| `detection_agg` | 6×5-min detection buckets, wake counter | magic + CRC32 |
| `actuation` | sprays-today, day index, last-spray timestamp | magic + CRC32 |
| `roi_diff` | 64×48 reference thumbnail | magic |

After a cold boot every guard fails safe: counters reset to zero and the first
captured thumbnail only seeds the motion reference. Actuation cannot occur
until a later cycle produces enough independently classified ROIs.
