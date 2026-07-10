"""Tests for the manuscript-aligned statistical decision rules."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
ANALYSIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ANALYSIS_DIR))

import numpy as np  # noqa: E402

from actuation_reliability import exact_proportion  # noqa: E402
from battery_autonomy import autonomy_statistics  # noqa: E402
from detection_metrics import calculate_metrics  # noqa: E402
from mad_filter import mad_mask  # noqa: E402
from tost import tost_ind, tost_paired  # noqa: E402


class MadFilterTests(unittest.TestCase):
    def test_default_is_literal_three_mad(self):
        values = [0, 1, 2, 3, 6]
        self.assertEqual(mad_mask(values).tolist(),
                         [True, True, True, True, False])
        self.assertTrue(mad_mask(values, normal_consistency=True)[-1])

    def test_zero_mad_does_not_preserve_spike(self):
        self.assertEqual(mad_mask([1, 1, 1, 999]).tolist(),
                         [True, True, True, False])

    def test_all_nonfinite_values_are_dropped(self):
        self.assertFalse(mad_mask([np.nan, np.inf]).any())


class TostAndDetectionTests(unittest.TestCase):
    def test_zero_variance_inside_margin_is_equivalent(self):
        p_value, interval = tost_paired([1, 2, 3], [1, 2, 3], delta=1)
        self.assertEqual(p_value, 0.0)
        self.assertEqual(interval, (0.0, 0.0))

    def test_zero_variance_at_margin_is_not_equivalent(self):
        p_value, _ = tost_ind([2, 2, 2], [1, 1, 1], delta=1)
        self.assertEqual(p_value, 1.0)

    def test_invalid_margin_and_small_sample_fail(self):
        with self.assertRaises(ValueError):
            tost_paired([1, 2], [1, 2], delta=0)
        with self.assertRaises(ValueError):
            tost_ind([1], [1, 2], delta=1)

    def test_detection_report_runs_paired_tost_at_one_instance(self):
        truth = np.tile([0, 3, 5, 8, 12], 20)
        report = calculate_metrics(truth, truth, delta=1, alpha=0.05)
        self.assertEqual(report["f1"], 1.0)
        self.assertEqual(report["negative_f1"], 1.0)
        self.assertTrue(report["paired_tost"]["equivalent"])


class ReliabilityAndAutonomyTests(unittest.TestCase):
    def test_exact_reliability_requires_confidence_bound(self):
        result = exact_proportion(135, 135, 0.95, "greater")
        self.assertTrue(result["target_demonstrated"])
        self.assertGreater(result["one_sided_bound"], 0.95)

    def test_45_zero_false_sprays_are_not_enough_to_prove_five_percent(self):
        result = exact_proportion(0, 45, 0.05, "less")
        self.assertFalse(result["target_demonstrated"])
        enough = exact_proportion(0, 59, 0.05, "less")
        self.assertTrue(enough["target_demonstrated"])

    def test_autonomy_uses_independent_night_inference(self):
        passing = autonomy_statistics([-4.0] * 7, target_days=7)
        failing = autonomy_statistics([-7.0] * 7, target_days=7)
        self.assertTrue(passing["target_demonstrated"])
        self.assertGreater(passing["projected_days"], 7)
        self.assertFalse(failing["target_demonstrated"])


if __name__ == "__main__":
    unittest.main()
