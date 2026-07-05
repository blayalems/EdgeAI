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
    p1 = 1.0 - stats.t.cdf(t_lower, df)
    p2 = stats.t.cdf(t_upper, df)
    return max(p1, p2)


def tost_ind(a, b, delta, alpha=0.05):
    """Independent-sample (Welch) TOST. Returns (p, (ci_lo, ci_hi)) where
    the CI is the (1-2*alpha) interval used for the equivalence decision:
    equivalence at level alpha iff the CI lies inside (-delta, +delta)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    va, vb = a.var(ddof=1), b.var(ddof=1)
    na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    d = a.mean() - b.mean()
    p = _one_sided_ps(d, se, df, delta)
    tcrit = stats.t.ppf(1 - alpha, df)
    return p, (d - tcrit * se, d + tcrit * se)


def tost_paired(a, b, delta, alpha=0.05):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) != len(b):
        raise ValueError("paired TOST needs equal-length samples")
    diff = a - b
    n = len(diff)
    se = diff.std(ddof=1) / math.sqrt(n)
    p = _one_sided_ps(diff.mean(), se, n - 1, delta)
    tcrit = stats.t.ppf(1 - alpha, n - 1)
    return p, (diff.mean() - tcrit * se, diff.mean() + tcrit * se)


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
