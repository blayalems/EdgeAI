#!/usr/bin/env python3
"""Create and freeze a deterministic, per-class 70/15/15 image split.

The first run ranks files independently inside every class using a stable
SHA-256 key, allocates exact (subject to integer rounding) train/validation/
test counts, and freezes the complete dataset in ``split_manifest.json``.
Later runs verify membership, labels, and file content before reproducing the
CSV files; they never let a newly added image drift into the held-out test set.

Older releases wrote only ``test_manifest.json``.  That format is migrated
automatically: its held-out membership is preserved exactly, the current
train/validation population is stratified, and the complete dataset is then
frozen.  Migration cannot retroactively make an old hash split exactly 15% per
class, so any deviation is reported explicitly.

Expected layout (class index 0 must be the negative class)::

    data_root/
      negative/*.jpg
      thrips_hawaiiensis/*.jpg
      erionota_thrax/*.jpg
      pentalonia_nigronervosa/*.jpg

Outputs ``splits/{train,val,test}.csv`` plus versioned JSON manifests.  This
module is standard-library only and does not require TensorFlow.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import bg_config as cfg

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
MANIFEST_VERSION = 2
ALGORITHM = "sha256-stratified-largest-remainder-v1"
SPLIT_ORDER = ("train", "val", "test")


class FrozenDatasetError(ValueError):
    """The live dataset no longer matches its frozen manifest."""


def normalized_rel_path(path: Path, data_root: Path) -> str:
    """Return a platform-independent relative path used by all manifests."""
    return path.relative_to(data_root).as_posix()


def stable_rank(rel_path: str) -> str:
    """Stable ordering key, independent of directory enumeration and machine."""
    payload = f"{cfg.SEED}\0{rel_path}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_fingerprint(manifest, split: str | None = None) -> str:
    """Fingerprint class mapping and frozen entries (optionally one split)."""
    if split is not None and split not in SPLIT_ORDER:
        raise ValueError(f"unknown split: {split}")
    entries = manifest.get("files", [])
    if split is not None:
        entries = [entry for entry in entries if entry.get("split") == split]
    payload = {
        "schema_version": manifest.get("schema_version"),
        "classes": manifest.get("classes"),
        "files": entries,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def allocation_counts(n: int) -> dict[str, int]:
    """Largest-remainder allocation with every class present in every split."""
    if n < len(SPLIT_ORDER):
        raise ValueError(
            f"each class needs at least {len(SPLIT_ORDER)} images for a "
            f"stratified train/val/test split; found {n}"
        )
    fractions = {
        "train": 1.0 - cfg.VAL_FRACTION - cfg.TEST_FRACTION,
        "val": cfg.VAL_FRACTION,
        "test": cfg.TEST_FRACTION,
    }
    if any(v <= 0 for v in fractions.values()) or not math.isclose(
            sum(fractions.values()), 1.0, abs_tol=1e-12):
        raise ValueError(f"invalid split fractions: {fractions}")

    quotas = {name: n * fraction for name, fraction in fractions.items()}
    counts = {name: math.floor(quota) for name, quota in quotas.items()}
    remainder = n - sum(counts.values())
    priority = {name: i for i, name in enumerate(SPLIT_ORDER)}
    ranked = sorted(
        SPLIT_ORDER,
        key=lambda name: (-(quotas[name] - counts[name]), priority[name]),
    )
    for name in ranked[:remainder]:
        counts[name] += 1

    # Small classes can round a 15% cell to zero. Move one sample from the
    # largest donor so downstream training/evaluation never silently omit it.
    for empty in (name for name in SPLIT_ORDER if counts[name] == 0):
        donor = max(SPLIT_ORDER, key=lambda name: (counts[name], -priority[name]))
        if counts[donor] <= 1:
            raise ValueError(f"cannot represent all splits with {n} images")
        counts[donor] -= 1
        counts[empty] = 1
    return counts


def discover(data_root: Path, class_names: list[str]):
    """Discover images as ``(relative_path, label, class_name)`` rows."""
    if not class_names or class_names[0] != "negative":
        raise ValueError("class index 0 must be named 'negative'")
    if len(set(class_names)) != len(class_names):
        raise ValueError("class names must be unique")

    rows = []
    seen = set()
    for label, name in enumerate(class_names):
        cdir = data_root / name
        if not cdir.is_dir():
            raise ValueError(f"class folder not found: {cdir}")
        files = sorted(
            p for p in cdir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMG_EXTS
        )
        if not files:
            raise ValueError(f"no images under {cdir}")
        for path in files:
            rel = normalized_rel_path(path, data_root)
            if rel in seen:
                raise ValueError(f"duplicate relative path: {rel}")
            seen.add(rel)
            rows.append((rel, label, name))
    return rows


def _group_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row[1], row[2])].append(row)
    return groups


def assign_stratified(rows, fixed_test_paths=None):
    """Assign rows per class, optionally preserving a legacy frozen test set."""
    fixed_test_paths = (None if fixed_test_paths is None else
                        {p.replace("\\", "/") for p in fixed_test_paths})
    assignments = {}
    target_counts = {}
    actual_counts = {}

    for (label, class_name), class_rows in sorted(_group_rows(rows).items()):
        ordered = sorted(class_rows, key=lambda row: (stable_rank(row[0]), row[0]))
        targets = allocation_counts(len(ordered))
        target_counts[class_name] = targets

        if fixed_test_paths is None:
            test_rows = ordered[:targets["test"]]
        else:
            test_rows = [row for row in ordered if row[0] in fixed_test_paths]
            if not test_rows:
                raise FrozenDatasetError(
                    f"legacy test manifest has no {class_name!r} sample; "
                    "preserving it would leave that class untestable"
                )
        test_paths = {row[0] for row in test_rows}
        remaining = [row for row in ordered if row[0] not in test_paths]
        n_val = targets["val"]
        if len(remaining) - n_val < 1:
            raise FrozenDatasetError(
                f"legacy test membership leaves no training sample for "
                f"{class_name!r}"
            )
        val_rows = remaining[:n_val]
        train_rows = remaining[n_val:]

        for split, selected in (("train", train_rows), ("val", val_rows),
                                ("test", test_rows)):
            for rel, _, _ in selected:
                assignments[rel] = split
        actual_counts[class_name] = {
            "train": len(train_rows), "val": len(val_rows),
            "test": len(test_rows),
        }

    if fixed_test_paths is not None:
        known = {row[0] for row in rows}
        missing = fixed_test_paths - known
        if missing:
            raise FrozenDatasetError(
                f"{len(missing)} legacy frozen test file(s) are missing: "
                + ", ".join(sorted(missing)[:5])
            )
    return assignments, target_counts, actual_counts


def create_manifest(data_root: Path, rows, class_names: list[str],
                    fixed_test_paths=None):
    assignments, targets, actual = assign_stratified(rows, fixed_test_paths)
    entries = []
    for rel, label, class_name in sorted(rows):
        entries.append({
            "path": rel,
            "label": label,
            "class_name": class_name,
            "split": assignments[rel],
            "sha256": file_sha256(data_root / Path(rel)),
        })
    return {
        "schema_version": MANIFEST_VERSION,
        "frozen": True,
        "algorithm": ALGORITHM,
        "seed": cfg.SEED,
        "fractions": {
            "train": 1.0 - cfg.VAL_FRACTION - cfg.TEST_FRACTION,
            "val": cfg.VAL_FRACTION,
            "test": cfg.TEST_FRACTION,
        },
        "classes": class_names,
        "origin": ("legacy-test-manifest-v1" if fixed_test_paths is not None
                   else "stratified-first-run"),
        "target_counts_per_class": targets,
        "actual_counts_per_class": actual,
        "files": entries,
    }


def validate_manifest(manifest, data_root: Path, rows,
                      class_names: list[str]):
    """Validate immutable membership, labels, split values, and file bytes."""
    if manifest.get("schema_version") != MANIFEST_VERSION:
        raise FrozenDatasetError("unsupported split manifest version")
    if manifest.get("classes") != class_names:
        raise FrozenDatasetError(
            f"class order changed: frozen={manifest.get('classes')}, "
            f"current={class_names}"
        )
    frozen_entries = {entry["path"]: entry for entry in manifest.get("files", [])}
    live_rows = {rel: (label, class_name) for rel, label, class_name in rows}
    added = set(live_rows) - set(frozen_entries)
    missing = set(frozen_entries) - set(live_rows)
    if added or missing:
        details = []
        if added:
            details.append("added: " + ", ".join(sorted(added)[:5]))
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)[:5]))
        raise FrozenDatasetError(
            "dataset membership changed after split freeze (" + "; ".join(details)
            + "). Use --regenerate only when intentionally invalidating prior results."
        )
    for rel, (label, class_name) in live_rows.items():
        entry = frozen_entries[rel]
        if (entry.get("label"), entry.get("class_name")) != (label, class_name):
            raise FrozenDatasetError(f"label or class changed for {rel}")
        if entry.get("split") not in SPLIT_ORDER:
            raise FrozenDatasetError(f"invalid frozen split for {rel}")
        if file_sha256(data_root / Path(rel)) != entry.get("sha256"):
            raise FrozenDatasetError(
                f"file content changed after split freeze: {rel}"
            )
    return manifest


def _atomic_json(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(out_dir: Path, manifest) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = manifest["files"]
    for split in SPLIT_ORDER:
        path = out_dir / f"{split}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["path", "label", "class_name"])
            writer.writerows(
                (entry["path"], entry["label"], entry["class_name"])
                for entry in entries if entry["split"] == split
            )

    test_entries = [entry for entry in entries if entry["split"] == "test"]
    # Commit the authoritative full manifest first. If the process stops before
    # the compatibility view is refreshed, the next run still validates and
    # reconstructs every derived output from this file.
    _atomic_json(out_dir / "split_manifest.json", manifest)
    _atomic_json(out_dir / "test_manifest.json", {
        "schema_version": MANIFEST_VERSION,
        "frozen": True,
        "classes": manifest["classes"],
        # Keep this legacy field so older evaluation tooling can still read it.
        "test_files": [entry["path"] for entry in test_entries],
        "files": test_entries,
    })


def verify_frozen_dataset(data_root: Path, out_dir: Path | None = None):
    """Verify the full manifest and every derived CSV before downstream use."""
    out_dir = out_dir or Path(__file__).parent / cfg.SPLITS_DIR
    split_path = out_dir / "split_manifest.json"
    if not split_path.exists():
        raise FrozenDatasetError(
            f"{split_path} not found; run split_dataset.py before training, "
            "quantization, or evaluation"
        )
    try:
        manifest = json.loads(split_path.read_text(encoding="utf-8"))
        class_names = manifest["classes"]
        rows = discover(data_root, class_names)
        validate_manifest(manifest, data_root, rows, class_names)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise FrozenDatasetError(f"invalid frozen split manifest: {exc}") from exc

    for split in SPLIT_ORDER:
        csv_path = out_dir / f"{split}.csv"
        if not csv_path.exists():
            raise FrozenDatasetError(f"derived split file is missing: {csv_path}")
        try:
            with csv_path.open(newline="", encoding="utf-8") as stream:
                actual = sorted(
                    (row["path"], int(row["label"]), row["class_name"])
                    for row in csv.DictReader(stream)
                )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise FrozenDatasetError(f"invalid derived split CSV: {csv_path}") from exc
        expected = sorted(
            (entry["path"], entry["label"], entry["class_name"])
            for entry in manifest["files"] if entry["split"] == split
        )
        if actual != expected:
            raise FrozenDatasetError(
                f"{csv_path} does not match the frozen manifest; rerun "
                "split_dataset.py to reconstruct derived CSVs"
            )
    return manifest


def load_or_create_manifest(data_root: Path, out_dir: Path, rows,
                            class_names: list[str], regenerate=False):
    split_path = out_dir / "split_manifest.json"
    legacy_path = out_dir / "test_manifest.json"
    if split_path.exists() and not regenerate:
        manifest = json.loads(split_path.read_text(encoding="utf-8"))
        return validate_manifest(manifest, data_root, rows, class_names), False

    fixed_test = None
    migrated = False
    if legacy_path.exists() and not split_path.exists() and not regenerate:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        if legacy.get("classes") and legacy["classes"] != class_names:
            raise FrozenDatasetError(
                f"legacy class order changed: frozen={legacy['classes']}, "
                f"current={class_names}"
            )
        fixed_test = legacy.get("test_files")
        if not isinstance(fixed_test, list) or not fixed_test:
            raise FrozenDatasetError("legacy test manifest has no test_files")
        migrated = True
    return create_manifest(data_root, rows, class_names, fixed_test), migrated


def _print_summary(manifest, out_dir: Path, migrated: bool) -> None:
    counts = {split: 0 for split in SPLIT_ORDER}
    for entry in manifest["files"]:
        counts[entry["split"]] += 1
    total = len(manifest["files"])
    print(f"{total} images -> " + ", ".join(
        f"{name}: {counts[name]} ({counts[name] / total:.1%})"
        for name in SPLIT_ORDER
    ))
    for name in manifest["classes"]:
        actual = manifest["actual_counts_per_class"][name]
        target = manifest["target_counts_per_class"][name]
        suffix = "" if actual == target else f"  target={target}"
        print(f"  {name:>24}: " + "  ".join(
            f"{split}={actual[split]}" for split in SPLIT_ORDER
        ) + suffix)
    if migrated:
        print("WARNING: migrated legacy test membership unchanged; any per-class "
              "test-count deviation above is historical and cannot be repaired "
              "without invalidating the held-out set.")
    print(f"frozen manifest: {out_dir / 'split_manifest.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--classes", nargs="+", default=cfg.CLASS_NAMES,
                        help="class folders in label order (0 must be negative)")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).parent / cfg.SPLITS_DIR)
    parser.add_argument(
        "--regenerate", action="store_true",
        help="replace the frozen split after dataset changes; this invalidates "
             "all evaluation results derived from the previous held-out set",
    )
    args = parser.parse_args()

    try:
        rows = discover(args.data_root, args.classes)
        args.out.mkdir(parents=True, exist_ok=True)
        manifest, migrated = load_or_create_manifest(
            args.data_root, args.out, rows, args.classes, args.regenerate
        )
        write_outputs(args.out, manifest)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        sys.exit(f"error: {exc}")
    _print_summary(manifest, args.out, migrated)


if __name__ == "__main__":
    main()
