# analysis/ - statistical analysis (Member 3, Weeks 16 & 28)

Manuscript-aligned analysis of the field and trial data. Figures share the
style in `figstyle.py` (serif, single-column 3.5 inch, 300 dpi).

```sh
pip install -r requirements.txt
```

| Script | What it does | Input |
|---|---|---|
| `mad_filter.py` | Literal 3xMAD outlier filter; optional normal-consistency scaling is explicit | any CSV column |
| `tost.py` | Welch or paired TOST with p-value and 90% CI at alpha=0.05 | two CSV columns |
| `detection_metrics.py` | Presence metrics plus paired count TOST at the pre-registered +/-1 margin, JSON/LaTeX, and figures | ground-truth CSV |
| `actuation_reliability.py` | Exact one-sided binomial tests and Clopper-Pearson bounds for >=95% correctness and <=5% false sprays | validated trial counts |
| `battery_autonomy.py` | Independent per-night discharge fits, projection, and one-sample inference against 7 days | `.db` or `received_at,batt_mv` CSV |
| `impact.py` | Pesticide liters and CO2e: targeted sprays vs calendar baseline | `.db` or `received_at,action` CSV |

Typical sequence:

```sh
python mad_filter.py field.csv --col soil_vwc_pct --out field_clean.csv
python detection_metrics.py trial1.csv --delta 1 --outdir out --latex
python actuation_reliability.py --correct 135 --trials 135 \
  --false-sprays 0 --non-spray-opportunities 59
python battery_autonomy.py ../backend/bananaguard.db --node bg-n01
python impact.py ../backend/bananaguard.db --baseline-per-week 1
```

Notes:

- Count equivalence is paired by presentation. `detection_metrics.py` uses the
  manuscript margin of +/-1 instance/frame unless `--delta` is overridden.
- `non-spray-opportunities` counts only trials whose ground-truth action was no
  spray, including saturated-soil gates. With zero observed false sprays, at
  least 59 such opportunities are needed for a one-sided 95% exact upper bound
  at or below 5%; 45 is not enough.
- The battery test works on independent nightly discharge slopes and compares
  them with the slope mathematically equivalent to 7-day zero-solar autonomy.
- Every assumption in `impact.py` is a CLI flag; cite and override deployment
  values rather than treating defaults as measured facts.
- Outputs land in `out/` (git-ignored).

Run the statistical contract tests with:

```sh
python -m unittest discover -s tests -v
```
