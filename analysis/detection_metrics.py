#!/usr/bin/env python3
"""Confusion matrices + per-class P/R/F1 tables from Phase-1 trials.

Input: the ground-truth CSV produced by test/ground_truth_logger.py with
`detected_n` filled in (columns used: true_density, detected_n). Two
levels of analysis:

  1. Presence/absence (the decision that matters for Eq. 2): a
     presentation is positive iff density > 0, a detection iff
     detected_n > 0. -> 2x2 confusion matrix, precision/recall/F1.
  2. Count agreement: detected_n vs true_density scatter + MAE, to show
     whether N̂_pest tracks actual density (calibration of the EIL).

Outputs a LaTeX-ready table (--latex), a JSON report, and manuscript
figures (confusion matrix heatmap + count-agreement scatter).

    python3 detection_metrics.py trial1.csv --outdir out/
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import figstyle
import matplotlib.pyplot as plt


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv")
    ap.add_argument("--outdir", type=Path, default=Path("out"))
    ap.add_argument("--latex", action="store_true")
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.csv).dropna(subset=["true_density", "detected_n"])
    truth = df["true_density"].astype(int) > 0
    det = df["detected_n"].astype(int) > 0

    tp = int((truth & det).sum())
    fp = int((~truth & det).sum())
    fn = int((truth & ~det).sum())
    tn = int((~truth & ~det).sum())
    p, r, f = prf(tp, fp, fn)
    acc = (tp + tn) / len(df)
    mae = float((df["detected_n"].astype(int)
                 - df["true_density"].astype(int)).abs().mean())

    print(f"{len(df)} presentations")
    print(f"  confusion [ [TP={tp}, FN={fn}], [FP={fp}, TN={tn}] ]")
    print(f"  precision={p:.3f} recall={r:.3f} F1={f:.3f} acc={acc:.3f}")
    print(f"  count MAE = {mae:.2f} specimens")

    if args.latex:
        print("\n% LaTeX table")
        print(r"\begin{tabular}{lcccc}")
        print(r"Metric & Precision & Recall & F1 & Accuracy \\ \hline")
        print(rf"Presence detection & {p:.3f} & {r:.3f} & {f:.3f} & "
              rf"{acc:.3f} \\")
        print(r"\end{tabular}")

    (args.outdir / "detection_metrics.json").write_text(json.dumps({
        "n": len(df), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": p, "recall": r, "f1": f, "accuracy": acc,
        "count_mae": mae}, indent=1))

    # Fig 1: 2x2 confusion heatmap
    cm = np.array([[tp, fn], [fp, tn]])
    fig, ax = plt.subplots()
    ax.imshow(cm, cmap="Greens")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center",
                color="black" if v < cm.max() * 0.6 else "white")
    ax.set_xticks([0, 1], ["detected", "not detected"])
    ax.set_yticks([0, 1], ["pest", "no pest"])
    ax.set_xlabel("Node output")
    ax.set_ylabel("Ground truth")
    ax.grid(False)
    figstyle.save(fig, args.outdir / "confusion_matrix.png")

    # Fig 2: count agreement
    fig, ax = plt.subplots()
    jitter = (np.random.default_rng(0).random(len(df)) - 0.5) * 0.25
    ax.scatter(df["true_density"].astype(int) + jitter,
               df["detected_n"].astype(int), s=12, alpha=0.6)
    lim = max(df["true_density"].max(), df["detected_n"].max()) + 1
    ax.plot([0, lim], [0, lim], ls="--", lw=0.8, color="grey",
            label="perfect")
    ax.set_xlabel("True density (specimens)")
    ax.set_ylabel("Detected count")
    ax.legend()
    figstyle.save(fig, args.outdir / "count_agreement.png")


if __name__ == "__main__":
    main()
