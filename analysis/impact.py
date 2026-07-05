#!/usr/bin/env python3
"""Pesticide + CO2 impact estimates: BananaGuard vs calendar spraying.

Compares actual spray events (backend SQLite DB or a CSV with columns
received_at,action) against the calendar-spraying baseline the region
uses, and converts the avoided applications into liters of mix and kg of
CO2-equivalent.

Assumption constants are CLI flags with defaults documented below — cite
your own sources for the manuscript and override as needed.

    python3 impact.py ../backend/bananaguard.db --days 30
    python3 impact.py sprays.csv --baseline-per-week 2
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

import figstyle
import matplotlib.pyplot as plt


def load_sprays(path: str) -> pd.Series:
    if path.endswith((".db", ".sqlite3")):
        conn = sqlite3.connect(path)
        df = pd.read_sql_query(
            "SELECT received_at, action FROM uplinks", conn)
    else:
        df = pd.read_csv(path)
    df["t"] = pd.to_datetime(df["received_at"], format="ISO8601", utc=True)
    return df.loc[df["action"] == "SPRAY", "t"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help=".db/.sqlite3 or .csv")
    ap.add_argument("--days", type=float, default=None,
                    help="observation window; default = log span")
    ap.add_argument("--baseline-per-week", type=float, default=1.0,
                    help="calendar applications/week (regional practice)")
    ap.add_argument("--liters-per-application", type=float, default=0.35,
                    help="mix volume per targeted spray event")
    ap.add_argument("--liters-baseline", type=float, default=2.0,
                    help="mix volume per blanket calendar application")
    ap.add_argument("--co2-per-liter", type=float, default=1.8,
                    help="kg CO2e per liter of mix (production+transport)")
    ap.add_argument("--outdir", type=Path, default=Path("out"))
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    sprays = load_sprays(args.source)
    if sprays.empty:
        raise SystemExit("no SPRAY events in the log")
    span_days = args.days or max(
        1.0, (sprays.max() - sprays.min()).total_seconds() / 86400)

    n_bg = len(sprays)
    liters_bg = n_bg * args.liters_per_application
    n_cal = args.baseline_per_week * span_days / 7.0
    liters_cal = n_cal * args.liters_baseline

    liters_avoided = max(0.0, liters_cal - liters_bg)
    cut_pct = 100.0 * liters_avoided / liters_cal if liters_cal else 0.0
    co2_saved = liters_avoided * args.co2_per_liter

    print(f"window: {span_days:.1f} days")
    print(f"  BananaGuard : {n_bg} targeted sprays  = {liters_bg:.1f} L")
    print(f"  calendar    : {n_cal:.1f} applications = {liters_cal:.1f} L")
    print(f"  pesticide cut : {cut_pct:.0f}%  ({liters_avoided:.1f} L avoided)")
    print(f"  CO2e saved    : {co2_saved:.1f} kg "
          f"(@{args.co2_per_liter} kg/L)")

    fig, ax = plt.subplots(figsize=(2.6, 2.4))
    bars = ax.bar(["Calendar", "BananaGuard"], [liters_cal, liters_bg],
                  color=["#b8b8b8", "#1E9B4B"], width=0.6)
    ax.bar_label(bars, fmt="%.1f L", fontsize=7)
    ax.set_ylabel(f"Pesticide mix over {span_days:.0f} days (L)")
    ax.set_title(f"-{cut_pct:.0f}% · {co2_saved:.0f} kg CO2e saved",
                 fontsize=8)
    figstyle.save(fig, args.outdir / "impact.png")


if __name__ == "__main__":
    main()
