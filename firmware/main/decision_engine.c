/**
 * @file decision_engine.c
 * @brief Eq. 2, evaluated in strict priority order:
 *        faults > Eq. 2 gate > safety lockouts > spray.
 */
#include "decision_engine.h"
#include "app_config.h"

bg_decision_t decision_evaluate(const bg_decision_in_t *in)
{
    bg_decision_t d;

    /* 1. Faults dominate everything: a node that can't trust its sensors
     *    must not dispense chemicals. */
    if (in->camera_fault) {
        d.action = BG_ACTION_FAULT;
        d.reason = BG_REASON_CAMERA_FAULT;
        return d;
    }
    if (in->soil_fault) {
        d.action = BG_ACTION_FAULT;
        d.reason = BG_REASON_SOIL_FAULT;
        return d;
    }
    if (in->actuator_fault) {
        d.action = BG_ACTION_FAULT;
        d.reason = BG_REASON_ACTUATION_FAULT;
        return d;
    }

    /* 2. Eq. 2:  spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe  */
    if (in->n_pest <= BG_N_EIL) {
        d.action = BG_ACTION_LOG;
        d.reason = BG_REASON_BELOW_EIL;
        return d;
    }
    if (!in->soil_safe) {
        d.action = BG_ACTION_LOG;
        d.reason = BG_REASON_SOIL_UNSAFE;
        return d;
    }

    /* 3. Eq. 2 satisfied — apply hard safety inhibitors. */
    if (in->sprays_today >= BG_MAX_SPRAYS_PER_DAY) {
        d.action = BG_ACTION_LOCKOUT;
        d.reason = BG_REASON_MAX_SPRAYS;
        return d;
    }
    if (in->min_since_last_spray < BG_SPRAY_MIN_GAP_MIN) {
        d.action = BG_ACTION_LOCKOUT;
        d.reason = BG_REASON_MIN_GAP;
        return d;
    }
    if (in->batt_mv < BG_BATT_MIN_SPRAY_MV) {
        d.action = BG_ACTION_LOCKOUT;
        d.reason = BG_REASON_LOW_BATTERY;
        return d;
    }

    d.action = BG_ACTION_SPRAY;
    d.reason = BG_REASON_SPRAY;
    return d;
}

const char *decision_action_str(bg_action_t a)
{
    switch (a) {
    case BG_ACTION_LOG:     return "LOG";
    case BG_ACTION_SPRAY:   return "SPRAY";
    case BG_ACTION_LOCKOUT: return "LOCKOUT";
    case BG_ACTION_FAULT:   return "FAULT";
    }
    return "?";
}

const char *decision_reason_str(bg_reason_t r)
{
    switch (r) {
    case BG_REASON_BELOW_EIL:    return "below_EIL";
    case BG_REASON_SOIL_UNSAFE:  return "soil_unsafe";
    case BG_REASON_SPRAY:        return "eq2_satisfied";
    case BG_REASON_MAX_SPRAYS:   return "max_sprays_day";
    case BG_REASON_MIN_GAP:      return "min_spray_gap";
    case BG_REASON_SOIL_FAULT:   return "soil_fault";
    case BG_REASON_CAMERA_FAULT: return "camera_fault";
    case BG_REASON_LOW_BATTERY:  return "low_battery";
    case BG_REASON_ACTUATION_REFUSED: return "actuation_refused";
    case BG_REASON_ACTUATION_FAULT:   return "actuation_fault";
    }
    return "?";
}
