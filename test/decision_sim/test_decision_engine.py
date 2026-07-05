#!/usr/bin/env python3
"""Week-13 unit tests: Eq. 2 must be verified on the host BEFORE any
firmware that can energize the solenoid is flashed.

Run: python3 test/decision_sim/test_decision_engine.py
"""
import itertools
import unittest

from decision_engine import (
    BG_BATT_MIN_SPRAY_MV, BG_MAX_SPRAYS_PER_DAY, BG_N_EIL,
    BG_SPRAY_MIN_GAP_MIN, UINT32_MAX, Action, DecisionIn, Reason,
    decision_evaluate,
)


def ev(**kw):
    return decision_evaluate(DecisionIn(**kw))


class Eq2Boundary(unittest.TestCase):
    """Eq. 2 is a strict inequality: N̂_pest > N_EIL, not >=."""

    def test_below_eil_logs(self):
        self.assertEqual(ev(n_pest=0, soil_safe=True),
                         (Action.LOG, Reason.BELOW_EIL))

    def test_exactly_eil_logs(self):
        self.assertEqual(ev(n_pest=BG_N_EIL, soil_safe=True),
                         (Action.LOG, Reason.BELOW_EIL))

    def test_eil_plus_one_sprays(self):
        self.assertEqual(ev(n_pest=BG_N_EIL + 1, soil_safe=True),
                         (Action.SPRAY, Reason.SPRAY))

    def test_soil_unsafe_never_sprays_however_many_pests(self):
        for n in (BG_N_EIL + 1, 50, 65535):
            self.assertEqual(ev(n_pest=n, soil_safe=False),
                             (Action.LOG, Reason.SOIL_UNSAFE))


class FaultsDominate(unittest.TestCase):
    """A node that can't trust its sensors must not dispense chemicals —
    faults win over everything, including a blatant infestation."""

    def test_camera_fault_wins(self):
        self.assertEqual(ev(n_pest=100, soil_safe=True, camera_fault=True),
                         (Action.FAULT, Reason.CAMERA_FAULT))

    def test_soil_fault_wins(self):
        self.assertEqual(ev(n_pest=100, soil_safe=True, soil_fault=True),
                         (Action.FAULT, Reason.SOIL_FAULT))

    def test_camera_fault_before_soil_fault(self):
        self.assertEqual(
            ev(n_pest=0, soil_safe=False, soil_fault=True, camera_fault=True),
            (Action.FAULT, Reason.CAMERA_FAULT))


class SafetyLockouts(unittest.TestCase):
    def test_daily_cap(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True, sprays_today=BG_MAX_SPRAYS_PER_DAY),
            (Action.LOCKOUT, Reason.MAX_SPRAYS))

    def test_min_gap(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True,
               min_since_last_spray=BG_SPRAY_MIN_GAP_MIN - 1),
            (Action.LOCKOUT, Reason.MIN_GAP))

    def test_gap_exactly_met_sprays(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True,
               min_since_last_spray=BG_SPRAY_MIN_GAP_MIN),
            (Action.SPRAY, Reason.SPRAY))

    def test_never_sprayed_sentinel_passes_gap(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True, min_since_last_spray=UINT32_MAX),
            (Action.SPRAY, Reason.SPRAY))

    def test_low_battery(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True, batt_mv=BG_BATT_MIN_SPRAY_MV - 1),
            (Action.LOCKOUT, Reason.LOW_BATTERY))

    def test_battery_exactly_min_sprays(self):
        self.assertEqual(
            ev(n_pest=9, soil_safe=True, batt_mv=BG_BATT_MIN_SPRAY_MV),
            (Action.SPRAY, Reason.SPRAY))


class ExhaustiveInvariants(unittest.TestCase):
    """Sweep ~50k input combinations and assert the two safety invariants
    that must hold for EVERY input, not just the hand-picked cases."""

    def test_invariants(self):
        n_pests = [0, 1, BG_N_EIL - 1, BG_N_EIL, BG_N_EIL + 1, 12, 65535]
        gaps = [0, BG_SPRAY_MIN_GAP_MIN - 1, BG_SPRAY_MIN_GAP_MIN, UINT32_MAX]
        batts = [3000, BG_BATT_MIN_SPRAY_MV - 1, BG_BATT_MIN_SPRAY_MV, 4200]
        sprays = [0, 1, BG_MAX_SPRAYS_PER_DAY - 1, BG_MAX_SPRAYS_PER_DAY]
        bools = [False, True]
        count = 0
        for (n, safe, sf, cf, mv, st, gap) in itertools.product(
                n_pests, bools, bools, bools, batts, sprays, gaps):
            action, _ = ev(n_pest=n, soil_safe=safe, soil_fault=sf,
                           camera_fault=cf, batt_mv=mv, sprays_today=st,
                           min_since_last_spray=gap)
            count += 1
            if action is Action.SPRAY:
                # Invariant 1: SPRAY implies Eq. 2 and no inhibitor.
                self.assertTrue(n > BG_N_EIL and safe and not sf and not cf
                                and mv >= BG_BATT_MIN_SPRAY_MV
                                and st < BG_MAX_SPRAYS_PER_DAY
                                and gap >= BG_SPRAY_MIN_GAP_MIN)
            if sf or cf:
                # Invariant 2: any fault implies never SPRAY.
                self.assertIs(action, Action.FAULT)
        self.assertEqual(count, len(n_pests) * 2 ** 3 * len(batts)
                         * len(sprays) * len(gaps))


if __name__ == "__main__":
    unittest.main(verbosity=1)
