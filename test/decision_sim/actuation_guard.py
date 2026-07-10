"""Host model of the independent hard interlocks in actuation.c."""
from __future__ import annotations

from decision_engine import (
    BG_BATT_MIN_SPRAY_MV,
    BG_MAX_SPRAYS_PER_DAY,
    BG_SPRAY_MIN_GAP_MIN,
)


def actuation_permitted(*, sensor_fault: bool, soil_safe: bool, batt_mv: int,
                        sprays_today: int, min_since_last_spray: int) -> bool:
    """Return whether the low-level driver may energize the relay.

    This duplicates safety checks intentionally: callers cannot bypass soil,
    battery, daily-cap or minimum-gap gates by skipping decision_evaluate().
    """
    return (
        not sensor_fault
        and soil_safe
        and batt_mv >= BG_BATT_MIN_SPRAY_MV
        and sprays_today < BG_MAX_SPRAYS_PER_DAY
        and min_since_last_spray >= BG_SPRAY_MIN_GAP_MIN
    )
