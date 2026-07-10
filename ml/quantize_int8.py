#!/usr/bin/env python3
"""Full-INT8 post-training quantization of the trained Keras model.

Inputs AND outputs are int8 (required: inference.cc feeds int8 tensors and
reads int8 scores; TFLite-Micro on ESP32 has no float kernels worth using).
The representative dataset is drawn from the TRAIN split only — never from
the frozen test set.

Usage:
    python quantize_int8.py DATA_ROOT [--model exports/pest_mnv2.keras]
        [--samples 200]

Output: exports/pest_mnv2_int8.tflite
Then:   python evaluate.py DATA_ROOT --model exports/pest_mnv2_int8.tflite
        python export_c_array.py exports/pest_mnv2_int8.tflite
"""
import argparse
import csv
import hashlib
from collections import defaultdict
from pathlib import Path

import numpy as np

import bg_config as cfg
from split_dataset import FrozenDatasetError, verify_frozen_dataset

HERE = Path(__file__).parent


def select_representative_rows(rows, samples: int):
    """Select a deterministic, proportionally stratified calibration sample."""
    if samples <= 0:
        raise ValueError("representative sample count must be positive")
    if not rows:
        raise ValueError("training manifest is empty")
    groups = defaultdict(list)
    for row in rows:
        try:
            label = int(row["label"])
            class_name = row["class_name"]
            path = row["path"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid training-manifest row") from exc
        groups[(label, class_name)].append(row)

    n = min(samples, len(rows))
    if n < len(groups):
        raise ValueError(
            f"{n} calibration samples cannot cover {len(groups)} classes; "
            "increase --samples"
        )
    ideal = {key: n * len(group) / len(rows) for key, group in groups.items()}
    counts = {key: 1 for key in groups}
    while sum(counts.values()) < n:
        candidates = [key for key, group in groups.items()
                      if counts[key] < len(group)]
        key = max(candidates, key=lambda item: (ideal[item] - counts[item],
                                                -item[0], item[1]))
        counts[key] += 1

    selected_by_class = {}
    for key, group in groups.items():
        ordered = sorted(group, key=lambda row: hashlib.sha256(
            f"{cfg.SEED}\0{row['path']}".encode("utf-8")).hexdigest())
        selected_by_class[key] = ordered[:counts[key]]

    # Interleave classes so converter calibration is not ordered in long blocks.
    selected = []
    max_count = max(map(len, selected_by_class.values()))
    for index in range(max_count):
        for key in sorted(selected_by_class):
            group = selected_by_class[key]
            if index < len(group):
                selected.append(group[index])
    return selected


def rep_dataset(data_root: Path, samples: int):
    with open(HERE / cfg.SPLITS_DIR / "train.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    rows = select_representative_rows(rows, samples)
    counts = defaultdict(int)
    for row in rows:
        counts[row["class_name"]] += 1
    print("calibration set: " + ", ".join(
        f"{name}={count}" for name, count in sorted(counts.items())
    ))

    def gen():
        try:
            import tensorflow as tf
        except ImportError as exc:
            raise RuntimeError("TensorFlow is required for quantization") from exc
        for r in rows:
            raw = tf.io.decode_image(
                tf.io.read_file(str(data_root / r["path"])),
                channels=cfg.INPUT_C, expand_animations=False)
            img = tf.image.resize(raw, [cfg.INPUT_H, cfg.INPUT_W])
            yield [tf.cast(img, tf.float32)[None, ...]]

    return gen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("--model", type=Path,
                    default=HERE / cfg.EXPORTS_DIR / "pest_mnv2.keras")
    ap.add_argument("--samples", type=int, default=200)
    args = ap.parse_args()

    try:
        verify_frozen_dataset(args.data_root)
    except FrozenDatasetError as exc:
        raise SystemExit(f"error: {exc}") from exc

    try:
        import tensorflow as tf
    except ImportError as exc:
        raise SystemExit("TensorFlow is required; install ml/requirements.txt") from exc

    model = tf.keras.models.load_model(args.model)
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep_dataset(args.data_root, args.samples)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    blob = conv.convert()

    out = HERE / cfg.EXPORTS_DIR / "pest_mnv2_int8.tflite"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)

    interp = tf.lite.Interpreter(model_content=blob)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    outp = interp.get_output_details()[0]
    assert inp["dtype"] == np.int8 and outp["dtype"] == np.int8, \
        "quantization did not produce int8 I/O"
    print(f"{out} — {len(blob)/1024:.0f} KB")
    print(f"input  {inp['shape'].tolist()} int8 "
          f"scale={inp['quantization'][0]:.6f} zp={inp['quantization'][1]}")
    print(f"output {outp['shape'].tolist()} int8 "
          f"scale={outp['quantization'][0]:.6f} zp={outp['quantization'][1]}")
    if len(blob) > 900 * 1024:
        print("WARNING: model close to/over the flash budget — consider a "
              "smaller alpha in bg_config.py")


if __name__ == "__main__":
    main()
