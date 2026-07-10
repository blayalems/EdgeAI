# ml/ - training pipeline (Member 2/3, Weeks 9-12)

MobileNetV2 transfer learning -> per-class evaluation -> full-INT8
quantization -> C-array export, plus host and on-device latency checks.

## Pipeline

```sh
pip install -r requirements.txt

# 1. Exact per-class 70/15/15 split, frozen before any training
python split_dataset.py /path/to/dataset
# folders: negative/, thrips_hawaiiensis/, erionota_thrax/,
#          pentalonia_nigronervosa/

# 2. Augmentation + two-phase transfer learning (head, then fine-tune)
python train_mobilenetv2.py /path/to/dataset

# 3. Float evaluation: per-class P/R/F1, macro F1, any-pest threshold
python evaluate.py /path/to/dataset

# 4. Full INT8 quantization; calibration is stratified and train-only
python quantize_int8.py /path/to/dataset

# 5. Re-evaluate the INT8 model on the identical frozen test population
python evaluate.py /path/to/dataset --model exports/pest_mnv2_int8.tflite

# 5b. Verify identical test fingerprints and report quantization deltas
python compare_evaluations.py runs/eval_pest_mnv2.json \
  runs/eval_pest_mnv2_int8.json --max-macro-f1-drop 0.02

# 6. Export into the firmware (replaces firmware/main/model_data.cc)
python export_c_array.py exports/pest_mnv2_int8.tflite
cd ../firmware && idf.py build
```

The two evaluation JSON files contain macro and weighted F1. Report the
float-to-INT8 deltas rather than only the post-quantization score.

## Latency: host sanity check vs on-device truth

```sh
python benchmark_latency.py exports/pest_mnv2_int8.tflite   # host, 1 thread
idf.py monitor | tee /tmp/bg_monitor.log                    # on the node
python device_latency.py /tmp/bg_monitor.log --budget-ms 400
```

`inference.cc` logs `invoke_us=<n>` for every classification;
`device_latency.py` turns a captured log into mean/p50/p95 and a PASS/FAIL
against the budget. The approximately 240 ms literature number came from a
dual-core Xtensa ESP32 and does not establish ESP32-C6 latency. Measure the
deployed C6 before retiring that risk.

## Class and firmware contracts

- Input is 96x96x3.
- Class 0 is negative/background. Classes 1-3 are the three manuscript target
  pests. Any non-zero class above the 60% confidence gate is pest presence;
  class-specific P/R/F1 remains in the evaluation report.
- `split_dataset.py --classes ...` preserves support for custom/legacy folder
  slugs, but index 0 must remain `negative`.
- `export_c_array.py` emits the `g_model_data` / `g_model_data_len` symbols
  declared in `firmware/main/model_data.h`, with `alignas(16)`.

## Frozen stratified split

`split_dataset.py` ranks images independently inside each class and uses
largest-remainder allocation, so each class receives the requested 70/15/15
proportions subject only to integer rounding. `splits/split_manifest.json`
freezes every path, label, split, and SHA-256 content digest. Later runs reject
added, removed, relabeled, or modified images instead of silently changing the
held-out population. Use `--regenerate` only when intentionally invalidating
all prior model-selection and test results.

An older `test_manifest.json` is migrated automatically. Existing held-out
membership is preserved exactly; any historical per-class imbalance is
reported because repairing it would contaminate the frozen test contract.

## Lightweight contract tests

```sh
python -m unittest discover -s tests -v
```

These tests cover split stratification/freezing/migration, calibration
coverage, and multi-pest operating-point metrics without importing TensorFlow.

## Low-code alternative

Edge Impulse Studio can replace data splitting, augmentation, training, and
INT8 export. Document the trade-off before mixing artifacts from the two paths.
