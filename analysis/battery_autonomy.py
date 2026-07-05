#!/usr/bin/env python3
"""Battery autonomy analysis from logged uplinks.

Reads batt_mv over time from the backend SQLite DB (or any CSV with
received_at + batt_mv columns), 3xMAD-cleans it, fits the overnight
discharge slope (no solar input) and reports:

  * mean overnight draw (mV/h and estimated mA using the pack curve),
  * projected autonomy in days from full charge with zero solar
    (the Table-III worst case),
  * a manuscript figure of the voltage trace + fit.

    python3 battery_autonomy.py ../backend/bananaguard.db --node bg-n01
    python3 battery_autonomy.py log.csv --capacity-mah 10000
"""
import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import figstyle
import matplotlib.pyplot as plt
from mad_filter import mad_mask

# Pack constants — EDIT to the deployed pack.
FULL_MV, EMPTY_MV = 4200, 3300         # single Li-ion cell window
NIGHT_HOURS = (20, 5)                  # LOCAL hours treated as solar-free
DEFAULT_TZ_OFFSET_H = 8.0              # Davao is UTC+8; --tz-offset overrides


def load(path: str, node: str | None) -> pd.DataFrame:
    if path.endswith((".db", ".sqlite3")):
        conn = sqlite3.connect(path)
        q = ("SELECT received_at, batt_mv FROM uplinks"
             + (" WHERE device_id = ?" if node else "")
             + " ORDER BY received_at")
        df = pd.read_sql_query(q, conn, params=(node,) if node else None)
    else:
        df = pd.read_csv(path)[["received_at", "batt_mv"]]
    df["t"] = pd.to_datetime(df["received_at"], format="ISO8601", utc=True)
    return df.dropna()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help=".db/.sqlite3 or .csv")
    ap.add_argument("--node", default=None)
    ap.add_argument("--capacity-mah", type=float, default=10000.0)
    ap.add_argument("--tz-offset", type=float, default=DEFAULT_TZ_OFFSET_H,
                    help="site UTC offset in hours; timestamps in the log "
                         "are UTC but the night window is local time")
    ap.add_argument("--outdir", type=Path, default=Path("out"))
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    df = load(args.source, args.node)
    if len(df) < 10:
        raise SystemExit(f"only {len(df)} samples — need a longer log")
    df = df[mad_mask(df["batt_mv"])]

    local_t = df["t"] + pd.Timedelta(hours=args.tz_offset)
    hrs = local_t.dt.hour
    night = df[(hrs >= NIGHT_HOURS[0]) | (hrs < NIGHT_HOURS[1])].copy()
    # A night spans local midnight; shifting local time by the morning
    # cutoff groups 20:00-04:59 into one contiguous night id.
    night["night_id"] = (local_t.loc[night.index]
                         - pd.Timedelta(hours=NIGHT_HOURS[1])).dt.date

    slopes, weights = [], []
    for _, g in night.groupby("night_id"):
        if len(g) < 5:
            continue
        x = (g["t"] - g["t"].iloc[0]).dt.total_seconds() / 3600.0
        s, _ = np.polyfit(x, g["batt_mv"], 1)
        slopes.append(s)
        weights.append(len(g))
    if not slopes:
        raise SystemExit("no night with >=5 samples — need a longer log")
    slope_mv_h = float(np.average(slopes, weights=weights))
    drain = max(1e-6, -slope_mv_h)

    span_mv = FULL_MV - EMPTY_MV
    autonomy_h = span_mv / drain
    # Rough mA estimate: assume the mV/h slope maps linearly onto capacity.
    est_ma = args.capacity_mah / autonomy_h

    print(f"{len(df)} samples ({df['t'].iloc[0]} → {df['t'].iloc[-1]})"
          + (f", node {args.node}" if args.node else ""))
    print(f"  overnight slope : {slope_mv_h:+.2f} mV/h "
          f"(mean of {len(slopes)} night fits)")
    print(f"  est. avg draw   : {est_ma:.1f} mA "
          f"(pack {args.capacity_mah:.0f} mAh linearized)")
    print(f"  zero-solar autonomy from full: {autonomy_h/24:.1f} days")
    verdict = "PASS" if autonomy_h / 24 >= 7 else "FAIL"
    print(f"  ≥7-day requirement: {verdict}")

    fig, ax = plt.subplots(figsize=(3.5, 2.2))
    ax.plot(df["t"], df["batt_mv"], lw=0.8, label="batt_mv")
    shade_done = False
    for _, g in night.groupby("night_id"):
        ax.axvspan(g["t"].iloc[0], g["t"].iloc[-1], alpha=0.10,
                   color="grey",
                   label=None if shade_done else
                   f"night windows (mean {slope_mv_h:+.1f} mV/h)")
        shade_done = True
    ax.set_ylabel("Battery (mV)")
    ax.set_xlabel("Time (UTC)")
    fig.autofmt_xdate()
    ax.legend()
    figstyle.save(fig, args.outdir / "battery_autonomy.png")


if __name__ == "__main__":
    main()
