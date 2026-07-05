#!/usr/bin/env python3
"""Ground-truth logging for Phase 1 trials (Weeks 13-15).

Merges two streams into one timestamped CSV:
  * the servo rig's RIG,<ms>,<trial>,<pos>,<density> lines (what was
    actually shown), read from a serial port or a captured file;
  * the node's detections, pulled from the backend (/api/history) or
    entered by hand.

Output rows: utc_time,source,trial,position,true_density,detected_n,note
— exactly what analysis/detection_metrics.py consumes for the confusion
matrices and F1 tables.

Modes:
  # live rig on a serial port (needs pyserial: pip install pyserial)
  python3 ground_truth_logger.py serial /dev/ttyACM0 --out trial1.csv

  # rig lines captured to a file (e.g. via cat /dev/ttyACM0 > rig.log)
  python3 ground_truth_logger.py file rig.log --out trial1.csv

  # manual entry (no rig): prompts for density/detections per presentation
  python3 ground_truth_logger.py manual --out trial1.csv

Then fill detected_n per row from the node log (backend `/api/history`,
the SPIFFS decisions.csv, or the TTN live-data tab) before analysis.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone

FIELDS = ["utc_time", "source", "trial", "position", "true_density",
          "detected_n", "note"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_rig_line(line):
    parts = line.strip().split(",")
    if len(parts) != 5 or parts[0] != "RIG":
        return None
    _, ms, trial, pos, density = parts
    return {"utc_time": now(), "source": "rig", "trial": trial,
            "position": pos, "true_density": density,
            "detected_n": "", "note": f"rig_ms={ms}"}


def write_rows(rows, out):
    w = csv.DictWriter(out, fieldnames=FIELDS)
    if out.tell() == 0:
        w.writeheader()
    for r in rows:
        w.writerow(r)
        out.flush()


def mode_file(args, out):
    n = 0
    with open(args.path) as f:
        rows = [r for r in (parse_rig_line(l) for l in f) if r]
        write_rows(rows, out)
        n = len(rows)
    print(f"{n} presentations -> {args.out}")


def mode_serial(args, out):
    try:
        import serial  # pyserial
    except ImportError:
        sys.exit("pyserial missing: pip install pyserial")
    with serial.Serial(args.path, 115200, timeout=1) as port:
        print(f"listening on {args.path} — Ctrl-C to stop")
        try:
            while True:
                line = port.readline().decode(errors="replace")
                if line.startswith("#"):
                    print(line.rstrip())
                r = parse_rig_line(line)
                if r:
                    write_rows([r], out)
                    print(f"logged: trial {r['trial']} pos {r['position']} "
                          f"density {r['true_density']}")
        except KeyboardInterrupt:
            print("\nstopped")


def mode_manual(args, out):
    print("Manual ground-truth entry — empty density ends the session.")
    trial = 1
    while True:
        density = input("true density shown (blank=quit): ").strip()
        if not density:
            break
        detected = input("detections reported by node (blank=unknown): ").strip()
        note = input("note (optional): ").strip()
        write_rows([{"utc_time": now(), "source": "manual", "trial": trial,
                     "position": "", "true_density": density,
                     "detected_n": detected, "note": note}], out)
        trial += 1
    print(f"done -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["serial", "file", "manual"])
    ap.add_argument("path", nargs="?",
                    help="serial port or captured rig log (not for manual)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.mode in ("serial", "file") and not args.path:
        ap.error(f"mode '{args.mode}' needs a path")

    with open(args.out, "a", newline="") as out:
        {"serial": mode_serial, "file": mode_file,
         "manual": mode_manual}[args.mode](args, out)


if __name__ == "__main__":
    main()
