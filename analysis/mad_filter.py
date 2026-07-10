#!/usr/bin/env python3
"""3xMAD outlier filter (pre-registered cleaning rule for all field data).

A sample x is an outlier iff |x - median| > 3 * MAD, matching the
pre-registered manuscript rule exactly. An optional normal-consistency scale
(1.4826 * MAD) is available for other analyses but is never implicit.

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


def mad_mask(x, k: float = K, normal_consistency: bool = False):
    """Boolean mask over x: True where the value is NOT a 3xMAD outlier.
    NaNs/infinities are marked False (dropped)."""
    if not np.isfinite(k) or k <= 0:
        raise ValueError("k must be a finite positive number")
    x = np.asarray(x, dtype=float)
    if x.ndim != 1:
        raise ValueError("mad_mask expects a one-dimensional sample")
    ok = np.isfinite(x)
    if not ok.any():
        return np.zeros(x.shape, dtype=bool)
    med = np.median(x[ok])
    mad = np.median(np.abs(x[ok] - med))
    if mad == 0.0:
        # The literal k*MAD threshold is zero. Keeping all finite values here
        # would let an arbitrarily large spike survive whenever >=50% of the
        # log is identical.
        return ok & (x == med)
    scale = MAD_TO_SIGMA if normal_consistency else 1.0
    lim = k * scale * mad
    return ok & (np.abs(x - med) <= lim)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv")
    ap.add_argument("--col", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("-k", type=float, default=K)
    ap.add_argument(
        "--normal-consistency", action="store_true",
        help="use 1.4826*MAD as a normal-distribution sigma estimate; omit "
             "to apply the manuscript's literal k*MAD rule",
    )
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    keep = mad_mask(df[args.col], args.k, args.normal_consistency)
    dropped = df.loc[~keep, args.col]
    df[keep].to_csv(args.out, index=False)
    print(f"{len(df)} rows -> kept {int(keep.sum())}, "
          f"dropped {len(dropped)} ({len(dropped)/max(len(df),1):.1%})")
    if len(dropped):
        print("dropped values:", ", ".join(f"{v:g}" for v in dropped[:20]))


if __name__ == "__main__":
    main()
