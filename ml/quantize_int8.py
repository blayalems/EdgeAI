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
import random
from pathlib import Path

import numpy as np
import tensorflow as tf

import bg_config as cfg

HERE = Path(__file__).parent


def rep_dataset(data_root: Path, samples: int):
    with open(HERE / cfg.SPLITS_DIR / "train.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    random.Random(cfg.SEED).shuffle(rows)

    def gen():
        for r in rows[:samples]:
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

    model = tf.keras.models.load_model(args.model)
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep_dataset(args.data_root, args.samples)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    blob = conv.convert()

    out = HERE / cfg.EXPORTS_DIR / "pest_mnv2_int8.tflite"
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
