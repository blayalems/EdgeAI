"""Host-side mirror of firmware/main/decision_engine.c — line for line.

Eq. 2:  spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe
evaluated in strict priority order: sensor/actuator faults > Eq. 2 gate > safety
lockouts > spray.

If decision_engine.c changes, this file and its tests change in the same
commit — test_decision_engine.py is the executable spec that must pass
BEFORE any firmware that can energize the solenoid is flashed (Week 13).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# MIRROR of app_config.h
BG_N_EIL = 5
BG_MAX_SPRAYS_PER_DAY = 4
BG_SPRAY_MIN_GAP_MIN = 30
BG_BATT_MIN_SPRAY_MV = 3500

UINT32_MAX = 0xFFFFFFFF  # min_since_last_spray sentinel: never sprayed


class Action(Enum):
    LOG = 0
    SPRAY = 1
    LOCKOUT = 2
    FAULT = 3


class Reason(Enum):
    BELOW_EIL = "below_EIL"
    SOIL_UNSAFE = "soil_unsafe"
    SPRAY = "eq2_satisfied"
    MAX_SPRAYS = "max_sprays_day"
    MIN_GAP = "min_spray_gap"
    SOIL_FAULT = "soil_fault"
    CAMERA_FAULT = "camera_fault"
    LOW_BATTERY = "low_battery"
    ACTUATION_REFUSED = "actuation_refused"
    ACTUATION_FAULT = "actuation_fault"


@dataclass
class DecisionIn:
    n_pest: int
    soil_safe: bool
    soil_fault: bool = False
    camera_fault: bool = False
    actuator_fault: bool = False
    batt_mv: int = 4000
    sprays_today: int = 0
    min_since_last_spray: int = UINT32_MAX


def decision_evaluate(inp: DecisionIn) -> tuple[Action, Reason]:
    # 1. Faults dominate everything.
    if inp.camera_fault:
        return Action.FAULT, Reason.CAMERA_FAULT
    if inp.soil_fault:
        return Action.FAULT, Reason.SOIL_FAULT
    if inp.actuator_fault:
        return Action.FAULT, Reason.ACTUATION_FAULT

    # 2. Eq. 2.
    if inp.n_pest <= BG_N_EIL:
        return Action.LOG, Reason.BELOW_EIL
    if not inp.soil_safe:
        return Action.LOG, Reason.SOIL_UNSAFE

    # 3. Hard safety inhibitors.
    if inp.sprays_today >= BG_MAX_SPRAYS_PER_DAY:
        return Action.LOCKOUT, Reason.MAX_SPRAYS
    if inp.min_since_last_spray < BG_SPRAY_MIN_GAP_MIN:
        return Action.LOCKOUT, Reason.MIN_GAP
    if inp.batt_mv < BG_BATT_MIN_SPRAY_MV:
        return Action.LOCKOUT, Reason.LOW_BATTERY

    return Action.SPRAY, Reason.SPRAY
