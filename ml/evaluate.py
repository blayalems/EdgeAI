#!/usr/bin/env python3
"""Evaluate a trained model on the FROZEN test set.

Works on both the float Keras model and the INT8 .tflite (so you can
quantify quantization loss with the exact same data and code path).
Reports per-class precision / recall / F1, the confusion matrix, and — for
the operating point the firmware actually uses — pest-class metrics at the
60 % confidence threshold (BG_CONF_THRESHOLD_PCT).

Usage:
    python evaluate.py DATA_ROOT                          # exports/pest_mnv2.keras
    python evaluate.py DATA_ROOT --model exports/pest_mnv2_int8.tflite

Writes runs/eval_<modelname>.json for the analysis notebooks.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

import bg_config as cfg

HERE = Path(__file__).parent


def load_test_set(data_root: Path):
    with open(HERE / cfg.SPLITS_DIR / "test.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("error: empty test manifest — run split_dataset.py")
    names = {}
    imgs, labels = [], []
    for r in rows:
        raw = tf.io.decode_image(tf.io.read_file(str(data_root / r["path"])),
                                 channels=cfg.INPUT_C, expand_animations=False)
        img = tf.image.resize(raw, [cfg.INPUT_H, cfg.INPUT_W])
        imgs.append(img.numpy())
        labels.append(int(r["label"]))
        names[int(r["label"])] = r["class_name"]
    class_names = [names[i] for i in sorted(names)]
    return np.stack(imgs).astype(np.float32), np.array(labels), class_names


def predict_keras(model_path: Path, imgs: np.ndarray) -> np.ndarray:
    model = tf.keras.models.load_model(model_path)
    return model.predict(imgs, verbose=0)


def predict_tflite(model_path: Path, imgs: np.ndarray) -> np.ndarray:
    interp = tf.lite.Interpreter(model_path=str(model_path))
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    probs = []
    for img in imgs:
        x = img
        if inp["dtype"] == np.int8:
            # uint8 pixel -> int8 domain, same mapping as inference.cc
            scale, zp = inp["quantization"]
            x = np.clip(np.round(img / 255.0 / scale + zp), -128, 127
                        ).astype(np.int8)
        interp.set_tensor(inp["index"], x[None, ...])
        interp.invoke()
        y = interp.get_tensor(out["index"])[0]
        if out["dtype"] == np.int8:
            scale, zp = out["quantization"]
            y = (y.astype(np.float32) - zp) * scale
        probs.append(y)
    return np.stack(probs)


def per_class_metrics(cm: np.ndarray):
    tp = np.diag(cm).astype(float)
    prec = np.divide(tp, cm.sum(0), out=np.zeros_like(tp), where=cm.sum(0) > 0)
    rec = np.divide(tp, cm.sum(1), out=np.zeros_like(tp), where=cm.sum(1) > 0)
    f1 = np.divide(2 * prec * rec, prec + rec,
                   out=np.zeros_like(tp), where=(prec + rec) > 0)
    return prec, rec, f1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("--model", type=Path,
                    default=HERE / cfg.EXPORTS_DIR / "pest_mnv2.keras")
    args = ap.parse_args()

    imgs, labels, class_names = load_test_set(args.data_root)
    n = len(class_names)
    if args.model.suffix == ".tflite":
        probs = predict_tflite(args.model, imgs)
    else:
        probs = predict_keras(args.model, imgs)
    preds = probs.argmax(1)

    cm = np.zeros((n, n), dtype=int)
    for t, p in zip(labels, preds):
        cm[t, p] += 1
    prec, rec, f1 = per_class_metrics(cm)
    acc = (preds == labels).mean()

    print(f"model: {args.model.name}   test images: {len(labels)}   "
          f"accuracy: {acc:.3f}\n")
    print(f"{'class':>12}  {'prec':>6} {'recall':>6} {'F1':>6} {'n':>5}")
    for i, name in enumerate(class_names):
        print(f"{name:>12}  {prec[i]:6.3f} {rec[i]:6.3f} {f1[i]:6.3f} "
              f"{cm[i].sum():5d}")

    print("\nconfusion matrix (rows = true, cols = predicted):")
    print(f"{'':>12} " + " ".join(f"{c[:8]:>8}" for c in class_names))
    for i, name in enumerate(class_names):
        print(f"{name:>12} " + " ".join(f"{v:8d}" for v in cm[i]))

    # Firmware operating point: pest := argmax == BG_CLASS_PEST (=1) AND
    # confidence >= 60 %. Everything else is "no pest".
    pest_flag = (preds == 1) & (probs.max(1) >= cfg.CONF_THRESHOLD)
    truth = labels == 1
    tp = int((pest_flag & truth).sum())
    fp = int((pest_flag & ~truth).sum())
    fn = int((~pest_flag & truth).sum())
    p_op = tp / (tp + fp) if tp + fp else 0.0
    r_op = tp / (tp + fn) if tp + fn else 0.0
    f_op = 2 * p_op * r_op / (p_op + r_op) if p_op + r_op else 0.0
    print(f"\nfirmware operating point (weevil @ conf>={cfg.CONF_THRESHOLD:.0%}):"
          f" precision={p_op:.3f} recall={r_op:.3f} F1={f_op:.3f}")

    (HERE / cfg.RUNS_DIR).mkdir(exist_ok=True)
    report = {
        "model": args.model.name, "accuracy": float(acc),
        "class_names": class_names, "confusion_matrix": cm.tolist(),
        "precision": prec.tolist(), "recall": rec.tolist(), "f1": f1.tolist(),
        "operating_point": {"threshold": cfg.CONF_THRESHOLD,
                            "precision": p_op, "recall": r_op, "f1": f_op},
    }
    out = HERE / cfg.RUNS_DIR / f"eval_{args.model.stem}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
