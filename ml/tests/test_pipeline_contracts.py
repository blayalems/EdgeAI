"""Dependency-light tests for dataset, calibration, and metric contracts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

import bg_config as cfg  # noqa: E402
import compare_evaluations  # noqa: E402
import evaluate  # noqa: E402
import quantize_int8  # noqa: E402
import split_dataset  # noqa: E402


class FrozenSplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.classes = list(cfg.CLASS_NAMES)
        for class_name in self.classes:
            folder = self.root / class_name / "nested"
            folder.mkdir(parents=True)
            for index in range(20):
                (folder / f"image_{index:02}.jpg").write_bytes(
                    f"{class_name}-{index}".encode()
                )

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_per_class_70_15_15_and_portable_paths(self):
        rows = split_dataset.discover(self.root, self.classes)
        manifest = split_dataset.create_manifest(self.root, rows, self.classes)
        self.assertEqual(split_dataset.allocation_counts(20),
                         {"train": 14, "val": 3, "test": 3})
        for counts in manifest["actual_counts_per_class"].values():
            self.assertEqual(counts, {"train": 14, "val": 3, "test": 3})
        self.assertTrue(all("\\" not in entry["path"]
                            for entry in manifest["files"]))
        split_dataset.validate_manifest(manifest, self.root, rows, self.classes)

    def test_membership_and_content_are_immutable(self):
        rows = split_dataset.discover(self.root, self.classes)
        manifest = split_dataset.create_manifest(self.root, rows, self.classes)
        added = self.root / self.classes[0] / "added.jpg"
        added.write_bytes(b"new")
        changed_rows = split_dataset.discover(self.root, self.classes)
        with self.assertRaisesRegex(split_dataset.FrozenDatasetError, "added"):
            split_dataset.validate_manifest(
                manifest, self.root, changed_rows, self.classes
            )
        added.unlink()
        first = self.root / Path(rows[0][0])
        first.write_bytes(b"changed bytes")
        with self.assertRaisesRegex(split_dataset.FrozenDatasetError,
                                    "content changed"):
            split_dataset.validate_manifest(
                manifest, self.root, rows, self.classes
            )

    def test_downstream_verification_rejects_edited_csv(self):
        rows = split_dataset.discover(self.root, self.classes)
        manifest = split_dataset.create_manifest(self.root, rows, self.classes)
        out = Path(self.temp.name) / "splits"
        split_dataset.write_outputs(out, manifest)
        split_dataset.verify_frozen_dataset(self.root, out)
        with (out / "train.csv").open("a", encoding="utf-8") as stream:
            stream.write("negative/fake.jpg,0,negative\n")
        with self.assertRaisesRegex(split_dataset.FrozenDatasetError,
                                    "does not match"):
            split_dataset.verify_frozen_dataset(self.root, out)

    def test_legacy_test_manifest_migrates_without_reassignment(self):
        rows = split_dataset.discover(self.root, self.classes)
        fixed = []
        for class_name in self.classes:
            fixed.append(next(row[0] for row in rows if row[2] == class_name))
        out = Path(self.temp.name) / "splits"
        out.mkdir()
        (out / "test_manifest.json").write_text(json.dumps({
            "frozen": True, "classes": self.classes, "test_files": fixed,
        }))
        manifest, migrated = split_dataset.load_or_create_manifest(
            self.root, out, rows, self.classes
        )
        self.assertTrue(migrated)
        actual_test = {entry["path"] for entry in manifest["files"]
                       if entry["split"] == "test"}
        self.assertEqual(actual_test, set(fixed))
        self.assertEqual(manifest["origin"], "legacy-test-manifest-v1")


class CalibrationAndMetricTests(unittest.TestCase):
    def test_calibration_sample_is_deterministic_and_covers_every_class(self):
        rows = []
        sizes = [50, 20, 15, 15]
        for label, size in enumerate(sizes):
            for index in range(size):
                rows.append({"path": f"c{label}/{index}.jpg",
                             "label": str(label),
                             "class_name": cfg.CLASS_NAMES[label]})
        first = quantize_int8.select_representative_rows(rows, 20)
        second = quantize_int8.select_representative_rows(rows, 20)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertEqual({int(row["label"]) for row in first}, {0, 1, 2, 3})

    def test_any_nonzero_class_counts_as_pest(self):
        labels = evaluate.np.array([0, 1, 2, 3])
        probs = evaluate.np.array([
            [0.9, 0.05, 0.03, 0.02],
            [0.1, 0.7, 0.1, 0.1],
            [0.1, 0.1, 0.7, 0.1],
            [0.1, 0.1, 0.1, 0.7],
        ])
        metrics = evaluate.any_pest_metrics(labels, probs, threshold=0.6)
        self.assertEqual((metrics["tp"], metrics["tn"]), (3, 1))
        self.assertEqual(metrics["f1"], 1.0)

    def test_quantization_comparison_requires_same_test_fingerprint(self):
        reference = {
            "model": "float.keras", "test_split_sha256": "abc",
            "class_names": ["negative", "pest"], "accuracy": 0.9,
            "macro_f1": 0.88, "weighted_f1": 0.89, "f1": [0.9, 0.86],
        }
        candidate = {
            "model": "int8.tflite", "test_split_sha256": "abc",
            "class_names": ["negative", "pest"], "accuracy": 0.89,
            "macro_f1": 0.86, "weighted_f1": 0.87, "f1": [0.88, 0.84],
        }
        comparison = compare_evaluations.compare_reports(reference, candidate)
        self.assertAlmostEqual(comparison["metrics"]["macro_f1"]["drop"], 0.02)
        candidate["test_split_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "identical frozen test"):
            compare_evaluations.compare_reports(reference, candidate)


if __name__ == "__main__":
    unittest.main()
