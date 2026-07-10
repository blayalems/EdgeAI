#!/usr/bin/env python3
"""Exact-binomial validation of actuation reliability and false sprays.

The manuscript pre-registers reliability >=95% and false-spray rate <=5%.
This script uses one-sided exact binomial tests and one-sided Clopper-Pearson
bounds, rather than treating an observed percentage as proof of the target.

Example for 135 correct decisions and zero false sprays among 45 conditions
where spraying was prohibited::

    python actuation_reliability.py --correct 135 --trials 135 \
        --false-sprays 0 --non-spray-opportunities 45 --out out/reliability.json

``non-spray-opportunities`` must count only trials whose ground-truth expected
action was no spray (including saturated-soil gates); it is not all trials.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import stats


def _validate(events: int, trials: int, target: float, alpha: float):
    if isinstance(events, (bool, np.bool_)) or isinstance(trials, (bool, np.bool_)):
        raise ValueError("counts must be integers")
    try:
        numeric_events, numeric_trials = float(events), float(trials)
    except (TypeError, ValueError) as exc:
        raise ValueError("counts must be integers") from exc
    if (not np.isfinite(numeric_events) or not np.isfinite(numeric_trials) or
            not numeric_events.is_integer() or not numeric_trials.is_integer()):
        raise ValueError("counts must be integers")
    events, trials = int(numeric_events), int(numeric_trials)
    if trials <= 0 or not 0 <= events <= trials:
        raise ValueError("require 0 <= events <= trials and trials > 0")
    if not np.isfinite(target) or not 0 < target < 1:
        raise ValueError("target must lie strictly between 0 and 1")
    if not np.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("alpha must lie strictly between 0 and 0.5")
    return events, trials


def exact_proportion(events: int, trials: int, target: float,
                     alternative: str, alpha: float = 0.05):
    """One-sided exact test and Clopper-Pearson confidence bound."""
    events, trials = _validate(events, trials, target, alpha)
    if alternative not in {"greater", "less"}:
        raise ValueError("alternative must be 'greater' or 'less'")
    estimate = events / trials
    p_value = float(stats.binomtest(
        events, trials, p=target, alternative=alternative
    ).pvalue)
    if events == 0:
        lower = 0.0
    else:
        lower = float(stats.beta.ppf(alpha, events, trials - events + 1))
    if events == trials:
        upper = 1.0
    else:
        upper = float(stats.beta.ppf(1 - alpha, events + 1,
                                     trials - events))
    if alternative == "greater":
        demonstrated = p_value < alpha and lower >= target
        bound = lower
    else:
        demonstrated = p_value < alpha and upper <= target
        bound = upper
    return {
        "events": events, "trials": trials, "estimate": estimate,
        "target": target, "alternative": alternative, "alpha": alpha,
        "p_value": p_value, "one_sided_bound": bound,
        "bound_type": "lower" if alternative == "greater" else "upper",
        "target_demonstrated": demonstrated,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correct", type=int, required=True,
                        help="correct actuation decisions")
    parser.add_argument("--trials", type=int, required=True,
                        help="all evaluated actuation decisions")
    parser.add_argument("--reliability-target", type=float, default=0.95)
    parser.add_argument("--false-sprays", type=int)
    parser.add_argument("--non-spray-opportunities", type=int)
    parser.add_argument("--false-spray-target", type=float, default=0.05)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if ((args.false_sprays is None) !=
            (args.non_spray_opportunities is None)):
        parser.error("--false-sprays and --non-spray-opportunities are a pair")
    try:
        reliability = exact_proportion(
            args.correct, args.trials, args.reliability_target,
            alternative="greater", alpha=args.alpha,
        )
        report = {"reliability": reliability}
        if args.false_sprays is not None:
            report["false_spray_rate"] = exact_proportion(
                args.false_sprays, args.non_spray_opportunities,
                args.false_spray_target, alternative="less", alpha=args.alpha,
            )
    except ValueError as exc:
        parser.error(str(exc))

    verdict = "PASS" if reliability["target_demonstrated"] else "NOT PROVEN"
    print(f"reliability {reliability['events']}/{reliability['trials']} = "
          f"{reliability['estimate']:.1%}; exact lower "
          f"{100*(1-args.alpha):.0f}% bound "
          f"{reliability['one_sided_bound']:.1%}; "
          f"p={reliability['p_value']:.4g} -> {verdict}")
    if "false_spray_rate" in report:
        false_rate = report["false_spray_rate"]
        verdict = ("PASS" if false_rate["target_demonstrated"]
                   else "NOT PROVEN")
        print(f"false sprays {false_rate['events']}/{false_rate['trials']} = "
              f"{false_rate['estimate']:.1%}; exact upper "
              f"{100*(1-args.alpha):.0f}% bound "
              f"{false_rate['one_sided_bound']:.1%}; "
              f"p={false_rate['p_value']:.4g} -> {verdict}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n",
                            encoding="utf-8")
        print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
