#!/usr/bin/env python3
"""Battery-autonomy analysis from logged uplinks.

The analysis 3xMAD-cleans voltage, fits one discharge slope per independent
solar-free night, projects zero-solar autonomy, and performs a one-sided
one-sample test against the discharge slope equivalent to the manuscript's
7-day requirement.  It also reports a conservative confidence-bound autonomy.

    python battery_autonomy.py ../backend/bananaguard.db --node bg-n01
    python battery_autonomy.py log.csv --capacity-mah 10000
"""
import argparse
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import figstyle
from mad_filter import mad_mask

FULL_MV, EMPTY_MV = 4200, 3300
NIGHT_HOURS = (20, 5)
DEFAULT_TZ_OFFSET_H = 8.0


def load(path: str, node: str | None) -> pd.DataFrame:
    if path.endswith((".db", ".sqlite3")):
        with sqlite3.connect(path) as conn:
            query = ("SELECT received_at, batt_mv FROM uplinks"
                     + (" WHERE device_id = ?" if node else "")
                     + " ORDER BY received_at")
            df = pd.read_sql_query(
                query, conn, params=(node,) if node else None
            )
    else:
        df = pd.read_csv(path)[["received_at", "batt_mv"]]
    df["t"] = pd.to_datetime(df["received_at"], format="ISO8601", utc=True)
    return df.dropna().sort_values("t").reset_index(drop=True)


def nightly_slopes(df: pd.DataFrame, tz_offset: float):
    """Return the night-window rows and one mV/hour fit per usable night."""
    local_t = df["t"] + pd.Timedelta(hours=tz_offset)
    hours = local_t.dt.hour
    night = df[(hours >= NIGHT_HOURS[0]) | (hours < NIGHT_HOURS[1])].copy()
    # Shifting by the morning cutoff groups 20:00--04:59 across midnight.
    night["night_id"] = (local_t.loc[night.index]
                         - pd.Timedelta(hours=NIGHT_HOURS[1])).dt.date
    slopes = []
    for _, group in night.groupby("night_id"):
        if len(group) < 5:
            continue
        x = ((group["t"] - group["t"].iloc[0]).dt.total_seconds()
             / 3600.0).to_numpy()
        if np.unique(x).size < 2 or x.max() - x.min() < 1.0:
            continue
        slope, _ = np.polyfit(x, group["batt_mv"].to_numpy(), 1)
        if np.isfinite(slope):
            slopes.append(float(slope))
    return night, np.asarray(slopes, dtype=float)


def autonomy_statistics(slopes_mv_h, full_mv=FULL_MV, empty_mv=EMPTY_MV,
                        target_days=7.0, alpha=0.05):
    """Test mean discharge against the slope equivalent to ``target_days``."""
    slopes = np.asarray(slopes_mv_h, dtype=float)
    if slopes.ndim != 1 or len(slopes) < 2 or not np.isfinite(slopes).all():
        raise ValueError("need at least two finite, independent nightly slopes")
    if (slopes >= 0).any():
        raise ValueError(
            "every solar-free nightly fit must show discharge; inspect "
            "non-negative nights for charging, clock, or sensor errors"
        )
    if not full_mv > empty_mv:
        raise ValueError("full_mv must be greater than empty_mv")
    if not np.isfinite(target_days) or target_days <= 0:
        raise ValueError("target_days must be positive")
    if not np.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("alpha must lie strictly between 0 and 0.5")

    span_mv = float(full_mv - empty_mv)
    threshold_slope = -span_mv / (target_days * 24.0)
    mean_slope = float(slopes.mean())
    sd = float(slopes.std(ddof=1))
    if sd == 0.0:
        p_value = 0.0 if mean_slope > threshold_slope else 1.0
        lower_slope = mean_slope
    else:
        standard_error = sd / np.sqrt(len(slopes))
        statistic = (mean_slope - threshold_slope) / standard_error
        p_value = float(stats.t.sf(statistic, len(slopes) - 1))
        lower_slope = float(
            mean_slope
            - stats.t.ppf(1 - alpha, len(slopes) - 1) * standard_error
        )

    projected_days = span_mv / (-mean_slope) / 24.0
    conservative_days = (float("inf") if lower_slope >= 0 else
                         span_mv / (-lower_slope) / 24.0)
    demonstrated = p_value < alpha and lower_slope >= threshold_slope
    return {
        "n_nights": len(slopes),
        "mean_slope_mv_h": mean_slope,
        "sd_slope_mv_h": sd,
        "target_days": float(target_days),
        "threshold_slope_mv_h": threshold_slope,
        "alpha": float(alpha),
        "p_value": p_value,
        "lower_slope_bound_mv_h": lower_slope,
        "projected_days": projected_days,
        "conservative_bound_days": conservative_days,
        "target_demonstrated": bool(demonstrated),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help=".db/.sqlite3 or .csv")
    parser.add_argument("--node", default=None)
    parser.add_argument("--capacity-mah", type=float, default=10000.0)
    parser.add_argument("--full-mv", type=float, default=FULL_MV)
    parser.add_argument("--empty-mv", type=float, default=EMPTY_MV)
    parser.add_argument("--target-days", type=float, default=7.0)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--tz-offset", type=float, default=DEFAULT_TZ_OFFSET_H,
                        help="site UTC offset; night window is local time")
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    args = parser.parse_args()
    if args.capacity_mah <= 0:
        parser.error("--capacity-mah must be positive")
    args.outdir.mkdir(parents=True, exist_ok=True)

    df = load(args.source, args.node)
    if len(df) < 10:
        raise SystemExit(f"only {len(df)} samples - need a longer log")
    df = df[mad_mask(df["batt_mv"])].copy()
    night, slopes = nightly_slopes(df, args.tz_offset)
    try:
        result = autonomy_statistics(
            slopes, full_mv=args.full_mv, empty_mv=args.empty_mv,
            target_days=args.target_days, alpha=args.alpha,
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc

    autonomy_h = result["projected_days"] * 24.0
    estimated_ma = args.capacity_mah / autonomy_h
    print(f"{len(df)} cleaned samples "
          f"({df['t'].iloc[0]} to {df['t'].iloc[-1]})"
          + (f", node {args.node}" if args.node else ""))
    print(f"  overnight slope : {result['mean_slope_mv_h']:+.2f} mV/h "
          f"(unweighted mean of {result['n_nights']} night fits)")
    print(f"  est. avg draw   : {estimated_ma:.1f} mA "
          f"(pack {args.capacity_mah:.0f} mAh linearized)")
    print(f"  zero-solar autonomy from full: {result['projected_days']:.1f} days")
    print(f"  one-sample H1: autonomy >= {args.target_days:g} days "
          f"(slope > {result['threshold_slope_mv_h']:+.2f} mV/h): "
          f"p={result['p_value']:.4g}")
    verdict = "PASS" if result["target_demonstrated"] else "NOT PROVEN"
    print(f"  conservative {100*(1-args.alpha):.0f}% lower autonomy bound: "
          f"{result['conservative_bound_days']:.1f} days -> {verdict}")

    fig, axis = plt.subplots(figsize=(3.5, 2.2))
    axis.plot(df["t"], df["batt_mv"], lw=0.8, label="batt_mv")
    shade_done = False
    for _, group in night.groupby("night_id"):
        axis.axvspan(
            group["t"].iloc[0], group["t"].iloc[-1], alpha=0.10,
            color="grey", label=(None if shade_done else
                                  "solar-free night windows"),
        )
        shade_done = True
    axis.set_ylabel("Battery (mV)")
    axis.set_xlabel("Time (UTC)")
    fig.autofmt_xdate()
    axis.legend()
    figstyle.save(fig, args.outdir / "battery_autonomy.png")


if __name__ == "__main__":
    main()
