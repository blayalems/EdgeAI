#!/usr/bin/env python3
"""MobileNetV2 transfer learning for BananaGuard (96x96x3, alpha=0.35).

Two phases:
  1. head    — ImageNet base frozen, train the new classifier head.
  2. finetune — unfreeze the top of the base at a 10x lower LR.

Reads the manifests produced by split_dataset.py (never touches test.csv —
that file exists only for evaluate.py). Augmentation is applied on-GPU via
Keras preprocessing layers, training data only.

Usage:
    python train_mobilenetv2.py DATA_ROOT [--epochs-head 15]
        [--epochs-finetune 25] [--batch 32] [--unfreeze 40]

Artifacts: exports/pest_mnv2.keras (best val loss) + runs/history.json.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

import bg_config as cfg

HERE = Path(__file__).parent


def read_manifest(split: str):
    path = HERE / cfg.SPLITS_DIR / f"{split}.csv"
    if not path.exists():
        raise SystemExit(f"error: {path} not found — run split_dataset.py first")
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [r["path"] for r in rows], [int(r["label"]) for r in rows]


def make_dataset(data_root: Path, split: str, batch: int, train: bool):
    paths, labels = read_manifest(split)
    n_classes = max(labels) + 1
    ds = tf.data.Dataset.from_tensor_slices(
        ([str(data_root / p) for p in paths], labels))

    def load(path, label):
        img = tf.io.decode_image(tf.io.read_file(path),
                                 channels=cfg.INPUT_C, expand_animations=False)
        img = tf.image.resize(img, [cfg.INPUT_H, cfg.INPUT_W])
        return tf.cast(img, tf.float32), label

    ds = ds.map(load, num_parallel_calls=tf.data.AUTOTUNE)
    if train:
        ds = ds.shuffle(len(paths), seed=cfg.SEED)
    return ds.batch(batch).prefetch(tf.data.AUTOTUNE), labels, n_classes


def build_model(n_classes: int):
    augment = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal"),
        tf.keras.layers.RandomRotation(0.08),
        tf.keras.layers.RandomZoom(0.15),
        tf.keras.layers.RandomTranslation(0.08, 0.08),
        tf.keras.layers.RandomBrightness(0.15, value_range=(0.0, 255.0)),
        tf.keras.layers.RandomContrast(0.15),
    ], name="augment")

    base = tf.keras.applications.MobileNetV2(
        input_shape=(cfg.INPUT_H, cfg.INPUT_W, cfg.INPUT_C),
        alpha=cfg.MNV2_ALPHA, include_top=False, weights="imagenet")
    base.trainable = False

    inputs = tf.keras.Input((cfg.INPUT_H, cfg.INPUT_W, cfg.INPUT_C))
    x = augment(inputs)
    x = tf.keras.applications.mobilenet_v2.preprocess_input(x)  # -> [-1, 1]
    x = base(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dropout(0.25)(x)
    outputs = tf.keras.layers.Dense(n_classes, activation="softmax")(x)
    return tf.keras.Model(inputs, outputs), base


def class_weights(labels):
    counts = np.bincount(labels)
    total = counts.sum()
    return {i: total / (len(counts) * c) for i, c in enumerate(counts)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("--epochs-head", type=int, default=15)
    ap.add_argument("--epochs-finetune", type=int, default=25)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--unfreeze", type=int, default=40,
                    help="how many top base layers to unfreeze in phase 2")
    args = ap.parse_args()

    tf.keras.utils.set_random_seed(cfg.SEED)

    train_ds, train_labels, n_classes = make_dataset(
        args.data_root, "train", args.batch, train=True)
    val_ds, _, _ = make_dataset(args.data_root, "val", args.batch, train=False)

    model, base = build_model(n_classes)
    weights = class_weights(train_labels)
    print(f"{n_classes} classes, class weights: "
          + ", ".join(f"{i}:{w:.2f}" for i, w in weights.items()))

    (HERE / cfg.EXPORTS_DIR).mkdir(exist_ok=True)
    (HERE / cfg.RUNS_DIR).mkdir(exist_ok=True)
    best_path = HERE / cfg.EXPORTS_DIR / "pest_mnv2.keras"
    callbacks = [
        tf.keras.callbacks.ModelCheckpoint(
            best_path, monitor="val_loss", save_best_only=True),
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=6, restore_best_weights=True),
    ]

    model.compile(optimizer=tf.keras.optimizers.Adam(args.lr),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    h1 = model.fit(train_ds, validation_data=val_ds, class_weight=weights,
                   epochs=args.epochs_head, callbacks=callbacks)

    base.trainable = True
    for layer in base.layers[:-args.unfreeze]:
        layer.trainable = False
    model.compile(optimizer=tf.keras.optimizers.Adam(args.lr / 10),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    h2 = model.fit(train_ds, validation_data=val_ds, class_weight=weights,
                   epochs=args.epochs_finetune, callbacks=callbacks)

    history = {"head": h1.history, "finetune": h2.history,
               "n_classes": n_classes, "alpha": cfg.MNV2_ALPHA,
               "seed": cfg.SEED}
    (HERE / cfg.RUNS_DIR / "history.json").write_text(
        json.dumps(history, indent=1, default=float))
    print(f"best model: {best_path}")
    print("next: python evaluate.py DATA_ROOT   (test set, per-class P/R/F1)")
    print("then: python quantize_int8.py DATA_ROOT")


if __name__ == "__main__":
    main()
