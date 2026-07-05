#!/usr/bin/env python3
"""Host-side inference latency benchmark for the INT8 model.

This gives a fast sanity number (op count / model size effects) but a HOST
NUMBER DOES NOT TRANSFER to the ESP32-C6: the cited ~240 ms figure was
measured on a dual-core Xtensa ESP32, and the C6 is a single-core RISC-V
without those SIMD kernels. The number that retires the Week-4 risk comes
from the device itself — flash the model, capture the serial log, and run
ml/device_latency.py on it.

Usage:
    python benchmark_latency.py exports/pest_mnv2_int8.tflite [--runs 200]
"""
import argparse
import statistics
import time
from pathlib import Path

import numpy as np

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from tensorflow.lite import Interpreter  # type: ignore


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tflite", type=Path)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--threads", type=int, default=1,
                    help="1 mimics the single-core C6 more honestly")
    args = ap.parse_args()

    interp = Interpreter(model_path=str(args.tflite),
                         num_threads=args.threads)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    rng = np.random.default_rng(0)
    x = rng.integers(-128, 128, size=inp["shape"], dtype=np.int8) \
        if inp["dtype"] == np.int8 else \
        rng.random(inp["shape"], dtype=np.float32)

    for _ in range(10):  # warm-up
        interp.set_tensor(inp["index"], x)
        interp.invoke()

    times = []
    for _ in range(args.runs):
        interp.set_tensor(inp["index"], x)
        t0 = time.perf_counter()
        interp.invoke()
        times.append((time.perf_counter() - t0) * 1000)

    times.sort()
    print(f"{args.tflite.name} — {args.runs} runs, {args.threads} thread(s)")
    print(f"  mean {statistics.mean(times):7.2f} ms")
    print(f"  p50  {times[len(times)//2]:7.2f} ms")
    print(f"  p95  {times[int(len(times)*0.95)]:7.2f} ms")
    print("\nHost numbers are NOT the ESP32-C6 numbers. On-device: flash the "
          "model, then\n  idf.py monitor | tee /tmp/bg_monitor.log\n"
          "  python device_latency.py /tmp/bg_monitor.log")


if __name__ == "__main__":
    main()
