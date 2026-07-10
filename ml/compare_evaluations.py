#!/usr/bin/env python3
"""Compare float and INT8 evaluation reports on the identical frozen test set.

    python compare_evaluations.py runs/eval_pest_mnv2.json \
        runs/eval_pest_mnv2_int8.json --max-macro-f1-drop 0.02

The command refuses to compare reports whose test fingerprints or class maps
differ, then reports accuracy, macro/weighted F1, and per-class F1 deltas.
"""
import argparse
import json
from pathlib import Path


def _number(report, field):
    value = report.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"report field {field!r} is missing or non-numeric")
    return float(value)


def compare_reports(reference, candidate):
    fingerprint = reference.get("test_split_sha256")
    if not fingerprint or fingerprint != candidate.get("test_split_sha256"):
        raise ValueError("reports do not use the identical frozen test split")
    classes = reference.get("class_names")
    if not isinstance(classes, list) or classes != candidate.get("class_names"):
        raise ValueError("report class mappings differ")
    reference_f1, candidate_f1 = reference.get("f1"), candidate.get("f1")
    if (not isinstance(reference_f1, list) or
            not isinstance(candidate_f1, list) or
            len(reference_f1) != len(classes) or
            len(candidate_f1) != len(classes)):
        raise ValueError("per-class F1 arrays do not match the class mapping")

    metrics = {}
    for field in ("accuracy", "macro_f1", "weighted_f1"):
        before, after = _number(reference, field), _number(candidate, field)
        metrics[field] = {
            "reference": before, "candidate": after, "delta": after - before,
            "drop": before - after,
        }
    per_class = []
    for name, before, after in zip(classes, reference_f1, candidate_f1):
        before, after = float(before), float(after)
        per_class.append({"class_name": name, "reference": before,
                          "candidate": after, "delta": after - before,
                          "drop": before - after})
    return {
        "test_split_sha256": fingerprint,
        "reference_model": reference.get("model"),
        "candidate_model": candidate.get("model"),
        "metrics": metrics,
        "per_class_f1": per_class,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="float evaluation JSON")
    parser.add_argument("candidate", type=Path, help="INT8 evaluation JSON")
    parser.add_argument("--max-macro-f1-drop", type=float, default=None)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.max_macro_f1_drop is not None and args.max_macro_f1_drop < 0:
        parser.error("--max-macro-f1-drop must be non-negative")
    try:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        report = compare_reports(reference, candidate)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    for name, values in report["metrics"].items():
        print(f"{name:>12}: {values['reference']:.4f} -> "
              f"{values['candidate']:.4f}  delta={values['delta']:+.4f}")
    print("per-class F1:")
    for values in report["per_class_f1"]:
        print(f"  {values['class_name']:>24}: {values['reference']:.4f} -> "
              f"{values['candidate']:.4f}  delta={values['delta']:+.4f}")
    if args.max_macro_f1_drop is not None:
        drop = report["metrics"]["macro_f1"]["drop"]
        passed = drop <= args.max_macro_f1_drop
        report["acceptance"] = {
            "max_macro_f1_drop": args.max_macro_f1_drop,
            "passed": passed,
        }
        print(f"macro-F1 drop <= {args.max_macro_f1_drop:.4f}: "
              f"{'PASS' if passed else 'FAIL'}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n",
                            encoding="utf-8")
        print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
