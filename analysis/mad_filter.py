#!/usr/bin/env python3
"""3xMAD outlier filter (pre-registered cleaning rule for all field data).

A sample x is an outlier iff |x - median| > 3 * 1.4826 * MAD, where
1.4826 scales MAD to the sigma of a normal distribution. Median/MAD are
robust (up to 50% breakdown), unlike mean/std which an outlier drags.

Library use:
    from mad_filter import mad_mask
    keep = mad_mask(series)          # boolean mask, True = keep

CLI: filter one CSV column, write the kept rows + a report:
    python3 mad_filter.py data.csv --col soil_vwc_pct --out clean.csv
"""
import argparse

import numpy as np
import pandas as pd

MAD_TO_SIGMA = 1.4826
K = 3.0


def mad_mask(x, k: float = K):
    """Boolean mask over x: True where the value is NOT a 3xMAD outlier.
    NaNs are marked False (dropped)."""
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    med = np.median(x[ok])
    mad = np.median(np.abs(x[ok] - med))
    if mad == 0.0:
        # Degenerate (>=50% identical values): fall back to exact-match keep
        return ok & (x == med) if (x[ok] == med).all() else ok
    lim = k * MAD_TO_SIGMA * mad
    return ok & (np.abs(x - med) <= lim)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv")
    ap.add_argument("--col", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("-k", type=float, default=K)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    keep = mad_mask(df[args.col], args.k)
    dropped = df.loc[~keep, args.col]
    df[keep].to_csv(args.out, index=False)
    print(f"{len(df)} rows -> kept {int(keep.sum())}, "
          f"dropped {len(dropped)} ({len(dropped)/max(len(df),1):.1%})")
    if len(dropped):
        print("dropped values:", ", ".join(f"{v:g}" for v in dropped[:20]))


if __name__ == "__main__":
    main()
