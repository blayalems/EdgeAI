#!/usr/bin/env python3
"""TOST equivalence test (two one-sided t-tests).

Classical t-tests can only fail to find a difference; TOST can positively
support equivalence: H0 is |mu1 - mu2| >= delta, rejected (equivalence
claimed) iff BOTH one-sided tests reject, i.e. p_tost = max(p1, p2) < alpha.
Used in Week 28 to show e.g. "yield under BananaGuard is equivalent to
calendar spraying within delta".

Library:
    from tost import tost_ind, tost_paired
    p, (lo, hi) = tost_ind(a, b, delta)

CLI (two CSV columns, independent samples by default):
    python3 tost.py data.csv --col-a yield_bg --col-b yield_cal --delta 0.5
    python3 tost.py data.csv --col-a before --col-b after --delta 2 --paired

Cross-checked against statsmodels.stats.weightstats.ttost_ind; kept on
scipy only so the whole folder needs just numpy/pandas/scipy/matplotlib.
"""
import argparse
import math

import numpy as np
import pandas as pd
from scipy import stats


def _one_sided_ps(diff_mean, se, df, delta):
    t_lower = (diff_mean + delta) / se   # H0: diff <= -delta
    t_upper = (diff_mean - delta) / se   # H0: diff >= +delta
    p1 = stats.t.sf(t_lower, df)
    p2 = stats.t.cdf(t_upper, df)
    return float(max(p1, p2))


def _validate_settings(delta, alpha):
    if not np.isfinite(delta) or delta <= 0:
        raise ValueError("delta must be a finite positive equivalence margin")
    if not np.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("alpha must lie strictly between 0 and 0.5")


def _sample(values, name):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError(f"{name} must contain at least two observations")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} contains a missing or non-finite value")
    return values


def _degenerate_result(diff_mean, delta):
    # With no estimated variation the CI collapses to the observed difference.
    # Boundary equality is not evidence that the CI lies strictly inside.
    p = 0.0 if abs(diff_mean) < delta else 1.0
    return p, (float(diff_mean), float(diff_mean))


def tost_ind(a, b, delta, alpha=0.05):
    """Independent-sample (Welch) TOST. Returns (p, (ci_lo, ci_hi)) where
    the CI is the (1-2*alpha) interval used for the equivalence decision:
    equivalence at level alpha iff the CI lies inside (-delta, +delta)."""
    _validate_settings(delta, alpha)
    a, b = _sample(a, "sample a"), _sample(b, "sample b")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    d = float(a.mean() - b.mean())
    if se == 0.0:
        return _degenerate_result(d, delta)
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = _one_sided_ps(d, se, df, delta)
    tcrit = stats.t.ppf(1 - alpha, df)
    return p, (float(d - tcrit * se), float(d + tcrit * se))


def tost_paired(a, b, delta, alpha=0.05):
    _validate_settings(delta, alpha)
    a, b = _sample(a, "sample a"), _sample(b, "sample b")
    if len(a) != len(b):
        raise ValueError("paired TOST needs equal-length samples")
    diff = a - b
    n = len(diff)
    se = diff.std(ddof=1) / math.sqrt(n)
    diff_mean = float(diff.mean())
    if se == 0.0:
        return _degenerate_result(diff_mean, delta)
    p = _one_sided_ps(diff_mean, se, n - 1, delta)
    tcrit = stats.t.ppf(1 - alpha, n - 1)
    return p, (float(diff_mean - tcrit * se),
               float(diff_mean + tcrit * se))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv")
    ap.add_argument("--col-a", required=True)
    ap.add_argument("--col-b", required=True)
    ap.add_argument("--delta", type=float, required=True,
                    help="equivalence margin (same units as the data)")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--paired", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if args.paired:
        # Pairing is by row: drop a row when EITHER value is missing.
        # Column-wise dropna would silently re-pair across subjects.
        pair = df[[args.col_a, args.col_b]].dropna()
        a = pair[args.col_a].to_numpy()
        b = pair[args.col_b].to_numpy()
    else:
        a = df[args.col_a].dropna().to_numpy()
        b = df[args.col_b].dropna().to_numpy()
    fn = tost_paired if args.paired else tost_ind
    p, (lo, hi) = fn(a, b, args.delta, args.alpha)

    kind = "paired" if args.paired else "independent (Welch)"
    print(f"TOST {kind}: n_a={len(a)} n_b={len(b)} "
          f"mean diff={np.mean(a)-np.mean(b):+.4g} margin ±{args.delta:g}")
    print(f"  {100*(1-2*args.alpha):.0f}% CI of diff: [{lo:+.4g}, {hi:+.4g}]")
    print(f"  p_TOST = {p:.4g}")
    if p < args.alpha:
        print(f"  EQUIVALENT within ±{args.delta:g} at alpha={args.alpha:g}")
    else:
        print("  equivalence NOT demonstrated (this is not evidence of a "
              "difference either)")


if __name__ == "__main__":
    main()
