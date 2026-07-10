#!/usr/bin/env python3
"""Confusion matrices + per-class P/R/F1 tables from Phase-1 trials.

Input: the ground-truth CSV produced by test/ground_truth_logger.py with
`detected_n` filled in (columns used: true_density, detected_n). Two
levels of analysis:

  1. Presence/absence (the decision that matters for Eq. 2): a
     presentation is positive iff density > 0, a detection iff
     detected_n > 0. -> 2x2 confusion matrix, precision/recall/F1.
  2. Count agreement: detected_n vs true_density scatter + MAE and a paired
     TOST with the pre-registered +/-1 instance/frame margin.

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
from tost import tost_paired


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def _count_array(values, name):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError(f"{name} needs at least two paired observations")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} contains a missing or non-finite value")
    if (values < 0).any() or not np.equal(values, np.floor(values)).all():
        raise ValueError(f"{name} must contain non-negative integer counts")
    return values.astype(int)


def calculate_metrics(true_density, detected_n, delta=1.0, alpha=0.05):
    """Return presence metrics and paired count-equivalence statistics."""
    true_counts = _count_array(true_density, "true_density")
    detected_counts = _count_array(detected_n, "detected_n")
    if len(true_counts) != len(detected_counts):
        raise ValueError("true_density and detected_n must be row-paired")

    truth = true_counts > 0
    detected = detected_counts > 0
    tp = int((truth & detected).sum())
    fp = int((~truth & detected).sum())
    fn = int((truth & ~detected).sum())
    tn = int((~truth & ~detected).sum())
    pest_p, pest_r, pest_f = prf(tp, fp, fn)
    negative_p, negative_r, negative_f = prf(tn, fn, fp)
    p_tost, (ci_lo, ci_hi) = tost_paired(
        detected_counts, true_counts, delta=delta, alpha=alpha
    )
    equivalent = (p_tost < alpha and ci_lo > -delta and ci_hi < delta)
    errors = detected_counts - true_counts
    return {
        "n": len(true_counts), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": pest_p, "recall": pest_r, "f1": pest_f,
        "negative_precision": negative_p, "negative_recall": negative_r,
        "negative_f1": negative_f,
        "macro_f1": (pest_f + negative_f) / 2.0,
        "accuracy": (tp + tn) / len(true_counts),
        "count_mae": float(np.abs(errors).mean()),
        "count_mean_error": float(errors.mean()),
        "paired_tost": {
            "margin": float(delta), "alpha": float(alpha),
            "confidence_level": float(1 - 2 * alpha),
            "p_value": float(p_tost),
            "ci": [float(ci_lo), float(ci_hi)],
            "equivalent": bool(equivalent),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv")
    ap.add_argument("--outdir", type=Path, default=Path("out"))
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--delta", type=float, default=1.0,
                    help="paired count-equivalence margin (default: +/-1)")
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.csv).dropna(subset=["true_density", "detected_n"])
    try:
        report = calculate_metrics(
            df["true_density"].to_numpy(), df["detected_n"].to_numpy(),
            delta=args.delta, alpha=args.alpha,
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    true_counts = _count_array(df["true_density"].to_numpy(), "true_density")
    detected_counts = _count_array(df["detected_n"].to_numpy(), "detected_n")

    print(f"{report['n']} presentations")
    print(f"  confusion [ [TP={report['tp']}, FN={report['fn']}], "
          f"[FP={report['fp']}, TN={report['tn']}] ]")
    print(f"  pest precision={report['precision']:.3f} "
          f"recall={report['recall']:.3f} F1={report['f1']:.3f}")
    print(f"  negative precision={report['negative_precision']:.3f} "
          f"recall={report['negative_recall']:.3f} "
          f"F1={report['negative_f1']:.3f}")
    print(f"  macro F1={report['macro_f1']:.3f} "
          f"accuracy={report['accuracy']:.3f}")
    print(f"  count MAE={report['count_mae']:.2f}; mean error="
          f"{report['count_mean_error']:+.2f} specimens")
    tost = report["paired_tost"]
    verdict = "EQUIVALENT" if tost["equivalent"] else "NOT EQUIVALENT"
    print(f"  paired TOST +/-{tost['margin']:g}: p={tost['p_value']:.4g}, "
          f"{100*tost['confidence_level']:.0f}% CI "
          f"[{tost['ci'][0]:+.3f}, {tost['ci'][1]:+.3f}] -> {verdict}")

    if args.latex:
        print("\n% LaTeX table")
        print(r"\begin{tabular}{lcccc}")
        print(r"Class & Precision & Recall & F1 & Accuracy \\ \hline")
        print(rf"Pest & {report['precision']:.3f} & "
              rf"{report['recall']:.3f} & {report['f1']:.3f} & "
              rf"{report['accuracy']:.3f} \\")
        print(rf"Negative & {report['negative_precision']:.3f} & "
              rf"{report['negative_recall']:.3f} & "
              rf"{report['negative_f1']:.3f} & -- \\")
        print(r"\end{tabular}")

    (args.outdir / "detection_metrics.json").write_text(
        json.dumps(report, indent=1) + "\n", encoding="utf-8")

    # Fig 1: 2x2 confusion heatmap
    cm = np.array([[report["tp"], report["fn"]],
                   [report["fp"], report["tn"]]])
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
    ax.scatter(true_counts + jitter, detected_counts, s=12, alpha=0.6)
    lim = max(true_counts.max(), detected_counts.max()) + 1
    ax.plot([0, lim], [0, lim], ls="--", lw=0.8, color="grey",
            label="perfect")
    ax.set_xlabel("True density (specimens)")
    ax.set_ylabel("Detected count")
    ax.legend()
    figstyle.save(fig, args.outdir / "count_agreement.png")


if __name__ == "__main__":
    main()
