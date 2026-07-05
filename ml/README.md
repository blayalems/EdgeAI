# ml/ — training pipeline (Member 2/3, Weeks 9–12)

MobileNetV2 transfer learning → per-class evaluation → full-INT8
quantization → C-array export into the firmware, plus the latency
benchmarks that retire the Week-4 ESP32-C6 risk.

## Pipeline

```sh
pip install -r requirements.txt

# 1. Split — test set is FROZEN before any training (splits/test_manifest.json)
python split_dataset.py /path/to/dataset            # negative/ weevil/ [...]

# 2. Train — augmentation + 2-phase transfer learning (head, then fine-tune)
python train_mobilenetv2.py /path/to/dataset

# 3. Evaluate float model on the frozen test set (per-class P/R/F1)
python evaluate.py /path/to/dataset

# 4. INT8 post-training quantization (representative data from train split)
python quantize_int8.py /path/to/dataset

# 5. Re-evaluate the INT8 model — quantization loss must be reported
python evaluate.py /path/to/dataset --model exports/pest_mnv2_int8.tflite

# 6. Export into the firmware (replaces firmware/main/model_data.cc)
python export_c_array.py exports/pest_mnv2_int8.tflite
cd ../firmware && idf.py build
```

## Latency: host sanity check vs on-device truth

```sh
python benchmark_latency.py exports/pest_mnv2_int8.tflite   # host, 1 thread
idf.py monitor | tee /tmp/bg_monitor.log                    # on the node
python device_latency.py /tmp/bg_monitor.log --budget-ms 400
```

`inference.cc` logs `invoke_us=<n>` for every classification;
`device_latency.py` turns a captured log into mean/p50/p95 and a
PASS/FAIL against the budget. **The ~240 ms literature number came from a
dual-core Xtensa ESP32 — do not trust it for the single-core RISC-V C6.
Run this in Week 4 before committing to the C6.**

## Contracts with the firmware

- Input 96×96×3, class 0 = negative, class 1 = weevil, confidence gate
  60 % — mirrored in `bg_config.py` from `firmware/main/app_config.h`.
- `export_c_array.py` emits the `g_model_data` / `g_model_data_len`
  symbols declared in `firmware/main/model_data.h`, `alignas(16)`.

## Frozen test set

`split_dataset.py` hashes each file's relative path into a split, then
writes `splits/test_manifest.json`. Files listed there can never leave
the test set on later runs (new photos may still hash into it). If a
manifest file goes missing from disk the script hard-fails rather than
silently shrinking the test set.

## Low-code alternative

Edge Impulse Studio replaces steps 1–5 (data split, augmentation,
training, INT8 export). Decide in the Week-2 trade-off doc; this folder
is the full-control path and the fallback.
