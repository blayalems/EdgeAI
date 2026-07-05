#!/usr/bin/env python3
"""Parse on-device inference timings out of a captured serial log.

inference.cc logs one line per classification:

    I (12345) infer: invoke_us=187342

Capture a session (idf.py monitor | tee monitor.log, or any serial dump)
with at least ~30 inferences, then:

    python device_latency.py monitor.log [--budget-ms 400]

Exit code is non-zero if p95 exceeds the budget — usable straight in CI or
in the Week-4 go/no-go note. Stdlib only.
"""
import argparse
import re
import statistics
import sys
from pathlib import Path

PATTERN = re.compile(r"invoke_us=(\d+)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path)
    ap.add_argument("--budget-ms", type=float, default=400.0,
                    help="latency budget for the go/no-go check")
    args = ap.parse_args()

    us = [int(m.group(1)) for m in PATTERN.finditer(args.log.read_text(
        errors="replace"))]
    if not us:
        sys.exit(f"error: no 'invoke_us=' lines in {args.log} — is the "
                 "model flashed (placeholder never runs inference)?")

    ms = sorted(u / 1000.0 for u in us)
    mean = statistics.mean(ms)
    p50 = ms[len(ms) // 2]
    p95 = ms[int(len(ms) * 0.95)]
    print(f"{len(ms)} on-device inferences from {args.log}")
    print(f"  mean {mean:7.1f} ms")
    print(f"  p50  {p50:7.1f} ms")
    print(f"  p95  {p95:7.1f} ms   (budget {args.budget_ms:.0f} ms)")

    if p95 > args.budget_ms:
        print("FAIL: p95 over budget — the single-core RISC-V C6 risk is "
              "REAL. Options: smaller alpha, 64x64 input, Xtensa ESP32-S3, "
              "or esp-nn optimized kernels.")
        sys.exit(1)
    print("PASS: within budget — Week-4 latency risk retired.")


if __name__ == "__main__":
    main()
