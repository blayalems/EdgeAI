#!/usr/bin/env python3
"""Scenario simulation: run the mirrored decision engine over multi-day
synthetic traces (fake counts + soil states) and print/emit the decision
sequence — the "verify Eq. 2 before hardware ever sprays" harness.

    python3 scenario_sim.py                 # run all scenarios, table out
    python3 scenario_sim.py --csv out.csv   # also write rows for analysis/

Each scenario is a list of 30-min cycles. The harness also carries the
actuation-side state (sprays_today, minutes since last spray) exactly the
way the firmware does across wake cycles, so lockout behaviour is
exercised end-to-end, not just per-call.
"""
import argparse
import csv

from decision_engine import (
    UINT32_MAX, Action, DecisionIn, decision_evaluate,
)

CYCLE_MIN = 30  # BG_SLEEP_MIN_DEFAULT


def run(name, cycles):
    """cycles: list of dicts with n_pest, soil_safe and optional faults."""
    sprays_today, since_spray, day_cycle = 0, UINT32_MAX, 0
    rows = []
    for i, c in enumerate(cycles):
        day_cycle += 1
        if day_cycle > 48:            # midnight: firmware resets the day cap
            day_cycle, sprays_today = 1, 0
        inp = DecisionIn(
            n_pest=c["n_pest"], soil_safe=c["soil_safe"],
            soil_fault=c.get("soil_fault", False),
            camera_fault=c.get("camera_fault", False),
            batt_mv=c.get("batt_mv", 3900),
            sprays_today=sprays_today,
            min_since_last_spray=since_spray)
        action, reason = decision_evaluate(inp)
        if action is Action.SPRAY:
            sprays_today += 1
            since_spray = 0
        elif since_spray != UINT32_MAX:
            since_spray += CYCLE_MIN
        rows.append({"scenario": name, "cycle": i, "n_pest": c["n_pest"],
                     "soil_safe": c["soil_safe"],
                     "action": action.name, "reason": reason.value,
                     "sprays_today": sprays_today})
    return rows


SCENARIOS = {
    # Rising infestation on dry soil: must stay LOG until N>5, then spray,
    # then be held by the 30-min gap (one spray per cycle max anyway) and
    # the daily cap after 4 sprays.
    "outbreak_dry": [
        {"n_pest": n, "soil_safe": True}
        for n in (0, 1, 3, 5, 6, 8, 9, 11, 12, 10, 9, 8)
    ],
    # Same outbreak during a wet week: not a single spray allowed.
    "outbreak_wet": [
        {"n_pest": n, "soil_safe": False}
        for n in (0, 2, 6, 9, 14, 18, 22, 25)
    ],
    # Sensor fault day: camera dies mid-outbreak — FAULT, never spray.
    "camera_fault_midway": [
        {"n_pest": 7, "soil_safe": True},
        {"n_pest": 9, "soil_safe": True, "camera_fault": True},
        {"n_pest": 12, "soil_safe": True, "camera_fault": True},
        {"n_pest": 8, "soil_safe": True},
    ],
    # Battery browns out during an infestation.
    "low_battery": [
        {"n_pest": 8, "soil_safe": True, "batt_mv": 3600},
        {"n_pest": 9, "soil_safe": True, "batt_mv": 3450},
        {"n_pest": 9, "soil_safe": True, "batt_mv": 3400},
    ],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=argparse.FileType("w"), default=None)
    args = ap.parse_args()

    all_rows = []
    for name, cycles in SCENARIOS.items():
        rows = run(name, cycles)
        all_rows += rows
        sprays = sum(r["action"] == "SPRAY" for r in rows)
        print(f"\n=== {name}  ({len(rows)} cycles, {sprays} sprays) ===")
        for r in rows:
            print(f"  c{r['cycle']:02d} n_pest={r['n_pest']:>3} "
                  f"soil_safe={str(r['soil_safe']):5} -> {r['action']:7} "
                  f"({r['reason']})")

    # Hard assertions — the point of the harness:
    assert not any(r["action"] == "SPRAY"
                   for r in all_rows if r["scenario"] == "outbreak_wet"), \
        "sprayed on wet soil!"
    assert not any(r["action"] == "SPRAY" for r in all_rows
                   if r["scenario"] == "camera_fault_midway"
                   and r["cycle"] in (1, 2)), "sprayed on camera fault!"
    dry = [r for r in all_rows if r["scenario"] == "outbreak_dry"]
    assert max(r["sprays_today"] for r in dry) <= 4, "daily cap breached!"
    print("\nAll scenario invariants hold — Eq. 2 verified on host.")

    if args.csv:
        w = csv.DictWriter(args.csv, fieldnames=list(all_rows[0]))
        w.writeheader()
        w.writerows(all_rows)
        print(f"rows -> {args.csv.name}")


if __name__ == "__main__":
    main()
