# analysis/ — statistical analysis (Member 3, Weeks 16 & 28)

Manuscript-grade analysis of the field and trial data. All figures share
the style in `figstyle.py` (serif, single-column 3.5″, 300 dpi).

```sh
pip install -r requirements.txt
```

| Script | What it does | Input |
|---|---|---|
| `mad_filter.py` | 3×MAD robust outlier filter (pre-registered cleaning rule); importable `mad_mask()` + CSV CLI | any CSV column |
| `tost.py` | TOST equivalence test (Welch independent or paired), scipy-only, prints p_TOST + 90 % CI | two CSV columns |
| `detection_metrics.py` | Presence/absence confusion matrix, P/R/F1 (+ LaTeX table), count-agreement MAE, two figures | ground-truth CSV from `test/ground_truth_logger.py` |
| `battery_autonomy.py` | Per-night discharge fits → mV/h, est. draw, zero-solar autonomy vs the ≥7-day requirement, trace figure | backend `.db` or CSV (`received_at,batt_mv`) |
| `impact.py` | Pesticide liters + CO₂e: targeted sprays vs calendar baseline, comparison figure | backend `.db` or CSV (`received_at,action`) |

Typical Week-28 sequence:

```sh
python3 mad_filter.py field.csv --col soil_vwc_pct --out field_clean.csv
python3 detection_metrics.py trial1.csv --outdir out --latex
python3 battery_autonomy.py ../backend/bananaguard.db --node bg-n01
python3 impact.py ../backend/bananaguard.db --baseline-per-week 1
python3 tost.py yields.csv --col-a yield_bg --col-b yield_cal --delta 1.5
```

Notes:
- Every assumption in `impact.py` (baseline frequency, liters per
  application, kg CO₂e/L) is a CLI flag — cite your sources and override.
- `tost.py` implements the two one-sided tests directly on scipy;
  statsmodels `ttost_ind` / pingouin `tost` give the same numbers if you
  prefer a library cross-check.
- Outputs land in `out/` (git-ignored).
