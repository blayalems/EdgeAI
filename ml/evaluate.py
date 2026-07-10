#!/usr/bin/env python3
"""Evaluate a trained model on the FROZEN test set.

Works on both the float Keras model and the INT8 .tflite (so you can
quantify quantization loss with the exact same data and code path).
Reports per-class precision / recall / F1, macro/weighted F1, the confusion
matrix, and any-pest metrics at the deployed 60% confidence threshold. Class
0 is negative; every non-zero class is a target pest.

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

import bg_config as cfg
from split_dataset import (FrozenDatasetError, manifest_fingerprint,
                           verify_frozen_dataset)

HERE = Path(__file__).parent


def load_test_set(data_root: Path):
    with open(HERE / cfg.SPLITS_DIR / "test.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("error: empty test manifest — run split_dataset.py")
    names = {}
    imgs, labels = [], []
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise SystemExit("TensorFlow is required to load/evaluate images; "
                         "install ml/requirements.txt") from exc
    for r in rows:
        raw = tf.io.decode_image(tf.io.read_file(str(data_root / r["path"])),
                                 channels=cfg.INPUT_C, expand_animations=False)
        img = tf.image.resize(raw, [cfg.INPUT_H, cfg.INPUT_W])
        imgs.append(img.numpy())
        labels.append(int(r["label"]))
        label = int(r["label"])
        existing = names.setdefault(label, r["class_name"])
        if existing != r["class_name"]:
            raise SystemExit(f"error: label {label} has conflicting class names")
    expected = list(range(max(names) + 1))
    if sorted(names) != expected:
        raise SystemExit(
            "error: held-out split does not contain every contiguous class; "
            "regenerate it with the stratified splitter"
        )
    class_names = [names[i] for i in expected]
    return np.stack(imgs).astype(np.float32), np.array(labels), class_names


def predict_keras(model_path: Path, imgs: np.ndarray) -> np.ndarray:
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise SystemExit("TensorFlow is required for Keras evaluation") from exc
    model = tf.keras.models.load_model(model_path)
    return model.predict(imgs, verbose=0)


def predict_tflite(model_path: Path, imgs: np.ndarray) -> np.ndarray:
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise SystemExit("TensorFlow is required for TFLite evaluation") from exc
    interp = tf.lite.Interpreter(model_path=str(model_path))
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    probs = []
    for img in imgs:
        x = img
        if inp["dtype"] == np.int8:
            # The converter calibrated the input on the SAME 0-255 pixel
            # values the rep dataset feeds (quantize_int8.py), so quantize
            # the raw pixel domain: q = pixel/scale + zp. With the usual
            # scale≈1, zp≈-128 this is exactly what inference.cc does
            # (uint8 - 128); dividing by 255 here would crush every image
            # to the bottom of the int8 range and corrupt the evaluation.
            scale, zp = inp["quantization"]
            if scale <= 0:
                raise ValueError("INT8 input tensor has an invalid quantization scale")
            x = np.clip(np.round(img / scale + zp), -128, 127
                        ).astype(np.int8)
        interp.set_tensor(inp["index"], x[None, ...])
        interp.invoke()
        y = interp.get_tensor(out["index"])[0]
        if out["dtype"] == np.int8:
            scale, zp = out["quantization"]
            if scale <= 0:
                raise ValueError("INT8 output tensor has an invalid quantization scale")
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


def confusion_matrix(labels: np.ndarray, preds: np.ndarray,
                     n_classes: int) -> np.ndarray:
    labels = np.asarray(labels)
    preds = np.asarray(preds)
    if labels.shape != preds.shape or labels.ndim != 1:
        raise ValueError("labels and predictions must be equal-length 1-D arrays")
    if n_classes < 2:
        raise ValueError("evaluation requires a negative and at least one pest class")
    if (not np.isfinite(labels).all() or not np.isfinite(preds).all() or
            not np.equal(labels, np.floor(labels)).all() or
            not np.equal(preds, np.floor(preds)).all()):
        raise ValueError("labels and predictions must be finite integers")
    if ((labels < 0).any() or (labels >= n_classes).any() or
            (preds < 0).any() or (preds >= n_classes).any()):
        raise ValueError("label or prediction lies outside the model class range")
    cm = np.zeros((n_classes, n_classes), dtype=int)
    np.add.at(cm, (labels.astype(int), preds.astype(int)), 1)
    return cm


def any_pest_metrics(labels: np.ndarray, probs: np.ndarray,
                     threshold: float):
    """Binary operating point where every non-zero class counts as a pest."""
    labels = np.asarray(labels)
    probs = np.asarray(probs, dtype=float)
    if probs.ndim != 2 or len(labels) != len(probs):
        raise ValueError("probabilities must be N x classes and align with labels")
    if probs.shape[1] < 2 or not np.isfinite(probs).all():
        raise ValueError("probabilities need at least two finite class columns")
    if (labels.ndim != 1 or not np.isfinite(labels).all() or
            not np.equal(labels, np.floor(labels)).all() or
            (labels < 0).any() or (labels >= probs.shape[1]).any()):
        raise ValueError("labels must be valid finite integer class indices")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence threshold must lie in [0, 1]")
    preds = probs.argmax(axis=1)
    pest_flag = (preds != 0) & (probs.max(axis=1) >= threshold)
    truth = labels != 0
    tp = int((pest_flag & truth).sum())
    fp = int((pest_flag & ~truth).sum())
    fn = int((~pest_flag & truth).sum())
    tn = int((~pest_flag & ~truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return {
        "threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("--model", type=Path,
                    default=HERE / cfg.EXPORTS_DIR / "pest_mnv2.keras")
    args = ap.parse_args()

    try:
        split_manifest = verify_frozen_dataset(args.data_root)
    except FrozenDatasetError as exc:
        raise SystemExit(f"error: {exc}") from exc

    imgs, labels, class_names = load_test_set(args.data_root)
    n = len(class_names)
    if args.model.suffix.lower() == ".tflite":
        probs = predict_tflite(args.model, imgs)
    else:
        probs = predict_keras(args.model, imgs)
    if probs.ndim != 2 or probs.shape != (len(labels), n):
        raise SystemExit(
            f"error: model returned {probs.shape}; expected ({len(labels)}, {n}) "
            f"for classes {class_names}"
        )
    preds = probs.argmax(1)

    cm = confusion_matrix(labels, preds, n)
    prec, rec, f1 = per_class_metrics(cm)
    acc = (preds == labels).mean()
    support = cm.sum(1)
    macro_f1 = float(f1.mean())
    weighted_f1 = float(np.average(f1, weights=support))

    print(f"model: {args.model.name}   test images: {len(labels)}   "
          f"accuracy: {acc:.3f}\n")
    print(f"{'class':>12}  {'prec':>6} {'recall':>6} {'F1':>6} {'n':>5}")
    for i, name in enumerate(class_names):
        print(f"{name:>12}  {prec[i]:6.3f} {rec[i]:6.3f} {f1[i]:6.3f} "
              f"{cm[i].sum():5d}")
    verdict = "PASS" if macro_f1 >= cfg.MEAN_F1_TARGET else "FAIL"
    print(f"macro F1={macro_f1:.3f}  weighted F1={weighted_f1:.3f}  "
          f"objective (>={cfg.MEAN_F1_TARGET:.0%} macro F1): {verdict}")

    print("\nconfusion matrix (rows = true, cols = predicted):")
    print(f"{'':>12} " + " ".join(f"{c[:8]:>8}" for c in class_names))
    for i, name in enumerate(class_names):
        print(f"{name:>12} " + " ".join(f"{v:8d}" for v in cm[i]))

    operating = any_pest_metrics(labels, probs, cfg.CONF_THRESHOLD)
    print(f"\nany-pest operating point (classes 1..{n - 1} @ "
          f"conf>={cfg.CONF_THRESHOLD:.0%}): "
          f"precision={operating['precision']:.3f} "
          f"recall={operating['recall']:.3f} F1={operating['f1']:.3f}")

    (HERE / cfg.RUNS_DIR).mkdir(exist_ok=True)
    report = {
        "model": args.model.name, "accuracy": float(acc),
        "n_test": len(labels),
        "test_split_sha256": manifest_fingerprint(split_manifest, "test"),
        "class_names": class_names, "confusion_matrix": cm.tolist(),
        "precision": prec.tolist(), "recall": rec.tolist(), "f1": f1.tolist(),
        "support": support.tolist(), "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "mean_f1_target": cfg.MEAN_F1_TARGET,
        "mean_f1_target_met": macro_f1 >= cfg.MEAN_F1_TARGET,
        "operating_point": operating,
    }
    out = HERE / cfg.RUNS_DIR / f"eval_{args.model.stem}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
