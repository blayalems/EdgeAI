#!/usr/bin/env python3
"""Deterministic train/val/test split with a FROZEN test set.

Why frozen: the test set must be fixed *before* any training run so that
no augmentation choice, hyper-parameter sweep or early-stopping decision
can leak into it. This script:

  1. Assigns each image to a split by hashing its *relative path*
     (sha1 -> [0,1)), so the assignment is stable across machines,
     re-orderings and re-runs — no RNG state involved.
  2. On the first run, writes splits/test_manifest.json listing every
     test file. On later runs it REFUSES to remove or reassign anything
     already in that manifest (new files may still join the test set via
     the same hash rule; existing ones never leave).

Expected layout (one folder per class, class 0 = negative):

    data_root/
      negative/*.jpg
      weevil/*.jpg
      [extra_pest_class/*.jpg ...]

Outputs splits/{train,val,test}.csv with columns: path,label,class_name.

Stdlib only — runs anywhere, no TensorFlow needed.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import bg_config as cfg

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def hash_unit(rel_path: str) -> float:
    """Stable [0,1) value from the file's relative path."""
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(1 << 48)


def assign_split(rel_path: str) -> str:
    u = hash_unit(rel_path)
    if u < cfg.TEST_FRACTION:
        return "test"
    if u < cfg.TEST_FRACTION + cfg.VAL_FRACTION:
        return "val"
    return "train"


def discover(data_root: Path, class_names):
    rows = []
    for label, name in enumerate(class_names):
        cdir = data_root / name
        if not cdir.is_dir():
            sys.exit(f"error: class folder not found: {cdir}")
        files = sorted(
            p for p in cdir.rglob("*") if p.suffix.lower() in IMG_EXTS
        )
        if not files:
            sys.exit(f"error: no images under {cdir}")
        for p in files:
            rows.append((str(p.relative_to(data_root)), label, name))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("--classes", nargs="+", default=cfg.CLASS_NAMES,
                    help="class folder names, index order (0 = negative)")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / cfg.SPLITS_DIR)
    args = ap.parse_args()

    rows = discover(args.data_root, args.classes)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "test_manifest.json"

    frozen = set()
    if manifest_path.exists():
        frozen = set(json.loads(manifest_path.read_text())["test_files"])

    splits = {"train": [], "val": [], "test": []}
    present = set()
    for rel, label, name in rows:
        present.add(rel)
        s = "test" if rel in frozen else assign_split(rel)
        splits[s].append((rel, label, name))

    missing = frozen - present
    if missing:
        sys.exit(
            f"error: {len(missing)} file(s) in the frozen test manifest are "
            f"missing from {args.data_root} — restore them or delete the "
            f"manifest ONLY if you accept invalidating all past results.\n  "
            + "\n  ".join(sorted(missing)[:10])
        )

    test_files = sorted(rel for rel, _, _ in splits["test"])
    manifest_path.write_text(json.dumps(
        {"frozen": True, "data_root": str(args.data_root),
         "classes": args.classes, "test_files": test_files}, indent=1))

    for s, items in splits.items():
        with open(args.out / f"{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["path", "label", "class_name"])
            w.writerows(items)

    total = len(rows)
    print(f"{total} images -> " + ", ".join(
        f"{s}: {len(v)} ({len(v)/total:.0%})" for s, v in splits.items()))
    print(f"test manifest frozen at {manifest_path} "
          f"({len(test_files)} files, {len(frozen)} were already frozen)")
    per_class = {}
    for s, items in splits.items():
        for _, _, name in items:
            per_class.setdefault(name, {}).setdefault(s, 0)
            per_class[name][s] += 1
    for name, d in per_class.items():
        print(f"  {name:>12}: " + "  ".join(
            f"{s}={d.get(s, 0)}" for s in ("train", "val", "test")))


if __name__ == "__main__":
    main()
