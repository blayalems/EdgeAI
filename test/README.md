# test/ — test & validation code (Weeks 13–15)

## decision_sim/ — verify Eq. 2 before hardware ever sprays

`decision_engine.py` is a line-for-line Python mirror of
`firmware/main/decision_engine.c` (constants included). Two harnesses run
against it:

```sh
cd test/decision_sim
python3 test_decision_engine.py   # unit tests: boundaries, fault priority,
                                  # lockouts + ~50k-combination invariant sweep
python3 scenario_sim.py           # multi-day traces: dry outbreak, wet week,
                                  # mid-outbreak camera fault, brown-out
python3 scenario_sim.py --csv decisions.csv   # rows for analysis/
```

The invariants the sweep enforces on every input combination:
1. `SPRAY` ⟹ Eq. 2 satisfied AND no fault AND no lockout AND battery OK.
2. any sensor fault ⟹ `FAULT` (never spray).

Rule: these tests pass **before** any firmware that can energize the
solenoid is flashed. If `decision_engine.c` changes, the mirror and tests
change in the same commit.

## servo_rig/ — specimen presenter (Arduino C++)

`servo_rig.ino`: servo carousel presenting 6 cards at known densities
(edit `DENSITIES[]` to match your disk). Serial commands `g<n>`, `r`
(one randomized trial: every density once, Fisher-Yates order), `a`
(5 trials), `h`, `?`. Each presentation emits
`RIG,<millis>,<trial>,<position>,<density>` at 115200 baud.

Deliberately Arduino: zero shared code with the ESP-IDF node, so it does
not preempt the Week-2 framework trade-off.

## ground_truth_logger.py — Phase 1 trial log

Turns rig serial output (live port or captured file) or manual entry into
`utc_time,source,trial,position,true_density,detected_n,note` CSV — the
input format of `analysis/detection_metrics.py`. See the file header for
the three modes.
