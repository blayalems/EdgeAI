#!/usr/bin/env python3
"""Focused host tests for firmware contracts that do not require ESP-IDF."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from actuation_guard import actuation_permitted
from decision_engine import (
    BG_BATT_MIN_SPRAY_MV,
    BG_MAX_SPRAYS_PER_DAY,
    BG_N_EIL,
    BG_SPRAY_MIN_GAP_MIN,
    UINT32_MAX,
)
from detection_agg import AGG_BUCKET_SEC, DetectionAggregator, UINT16_MAX
from roi_components import retained_component_sizes

ROOT = Path(__file__).resolve().parents[2]
CONFIG_TEXT = (ROOT / "firmware/main/app_config.h").read_text(encoding="utf-8")


def macro_int(name: str) -> int:
    match = re.search(rf"^#define\s+{re.escape(name)}\s+(\d+)\b",
                      CONFIG_TEXT, re.MULTILINE)
    if not match:
        raise AssertionError(f"integer macro {name} not found")
    return int(match.group(1))


def macro_float(name: str) -> float:
    match = re.search(rf"^#define\s+{re.escape(name)}\s+"
                      r"([-+]?\d+(?:\.\d+)?)f?\b",
                      CONFIG_TEXT, re.MULTILINE)
    if not match:
        raise AssertionError(f"numeric macro {name} not found")
    return float(match.group(1))


class AggregationWindow(unittest.TestCase):
    def test_epoch_bucket_zero_is_real_data(self):
        agg = DetectionAggregator()
        agg.add_at(0, 2)
        agg.add_at(1, 3)
        self.assertEqual(agg.count_at(1), 5)

    def test_zero_detection_wake_expires_stale_counts(self):
        agg = DetectionAggregator()
        agg.add_at(0, BG_N_EIL + 1)
        agg.add_at(30 * 60, 0)
        self.assertEqual(agg.count_at(30 * 60), 0)

    def test_read_alone_expires_stale_counts(self):
        agg = DetectionAggregator()
        agg.add_at(AGG_BUCKET_SEC, 9)
        self.assertEqual(agg.count_at(36 * 60), 0)

    def test_clock_rollback_discards_future_bucket(self):
        agg = DetectionAggregator()
        agg.add_at(60 * 60, 8)
        self.assertEqual(agg.count_at(0), 0)

    def test_bucket_and_total_saturate(self):
        agg = DetectionAggregator()
        agg.add_at(0, UINT16_MAX)
        agg.add_at(0, 100)
        self.assertEqual(agg.count_at(0), UINT16_MAX)


class ReachabilityContract(unittest.TestCase):
    def test_configured_per_frame_capacity_can_cross_strict_eil(self):
        max_rois = macro_int("BG_DIFF_MAX_ROIS")
        firmware_eil = macro_int("BG_N_EIL")
        self.assertEqual(firmware_eil, BG_N_EIL)
        self.assertGreater(max_rois, firmware_eil)

        agg = DetectionAggregator()
        agg.add_at(0, firmware_eil + 1)  # six classified ROIs in one frame
        self.assertGreater(agg.count_at(0), firmware_eil)

    def test_six_disconnected_subjects_become_six_rois(self):
        width = height = 12
        mask = [False] * (width * height)
        # Six separated 2x2 components, each at the configured minimum size.
        for x0, y0 in ((0, 0), (4, 0), (8, 0),
                       (0, 6), (4, 6), (8, 6)):
            for y in (y0, y0 + 1):
                for x in (x0, x0 + 1):
                    mask[y * width + x] = True
        sizes = retained_component_sizes(
            mask, width, height,
            min_pixels=macro_int("BG_DIFF_MIN_COMPONENT_PIXELS"),
            capacity=macro_int("BG_DIFF_MAX_ROIS"),
        )
        self.assertEqual(sizes, [4] * (BG_N_EIL + 1))

    def test_main_uses_multi_roi_classify_then_count(self):
        main = (ROOT / "firmware/main/main.c").read_text(encoding="utf-8")
        self.assertIn("roi_diff_detect_many", main)
        self.assertRegex(main, r"if \(res\.pest[^\n]+n_new\+\+")


class IndependentActuatorInterlock(unittest.TestCase):
    def permitted(self, **overrides):
        values = dict(sensor_fault=False, soil_safe=True,
                      batt_mv=BG_BATT_MIN_SPRAY_MV,
                      sprays_today=0,
                      min_since_last_spray=UINT32_MAX)
        values.update(overrides)
        return actuation_permitted(**values)

    def test_nominal_is_permitted(self):
        self.assertTrue(self.permitted())

    def test_each_inhibitor_independently_blocks(self):
        cases = (
            {"sensor_fault": True},
            {"soil_safe": False},
            {"batt_mv": BG_BATT_MIN_SPRAY_MV - 1},
            {"sprays_today": BG_MAX_SPRAYS_PER_DAY},
            {"min_since_last_spray": BG_SPRAY_MIN_GAP_MIN - 1},
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.permitted(**case))

    def test_current_soil_contract_is_saturation_only(self):
        self.assertEqual(macro_float("BG_SOIL_SAFE_MIN_PCT"), 0.0)
        self.assertGreater(macro_float("BG_SOIL_SAFE_MAX_PCT"), 0.0)


class CrossLayerStaticContracts(unittest.TestCase):
    def test_payload_remains_nine_bytes(self):
        source = (ROOT / "firmware/main/lora_telemetry.c").read_text(
            encoding="utf-8")
        self.assertRegex(source, r"#define\s+BG_UPLINK_LEN\s+9\b")

    def test_four_class_model_accepts_all_non_negative_targets(self):
        self.assertEqual(macro_int("BG_MODEL_CLASS_COUNT"), 4)
        source = (ROOT / "firmware/main/inference.cc").read_text(
            encoding="utf-8")
        self.assertIn("best != BG_CLASS_NEGATIVE", source)
        self.assertIn("in->params.scale", source)

    def test_unverified_hardware_assumptions_fail_closed(self):
        self.assertIn("#if !defined(CONFIG_IDF_TARGET_ESP32)", CONFIG_TEXT)
        self.assertIn("#error", CONFIG_TEXT)
        self.assertEqual(macro_int("BG_LORA_PLAN_VERIFIED"), 0)

    def test_real_radio_component_is_linked_when_present(self):
        cmake = (ROOT / "firmware/main/CMakeLists.txt").read_text(
            encoding="utf-8")
        self.assertIn("idf_component_optional_requires(PRIVATE ttn-esp32)",
                      cmake)


if __name__ == "__main__":
    unittest.main(verbosity=1)
