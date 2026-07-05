/**
 * @file decision_engine.h
 * @brief Pure decision logic implementing Eq. 2 of the design doc:
 *
 *        spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe
 *
 * plus the safety inhibitors (sensor fault, daily lockout, low battery)
 * that gate the actuation path. Pure function of its inputs — no I/O —
 * so it is host-unit-testable.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    BG_ACTION_LOG = 0,        /* below EIL and/or soil unsafe -> log only */
    BG_ACTION_SPRAY = 1,      /* Eq. 2 satisfied, no inhibitor */
    BG_ACTION_LOCKOUT = 2,    /* Eq. 2 satisfied but daily/gap lockout active */
    BG_ACTION_FAULT = 3,      /* sensor fault -> never spray */
} bg_action_t;

typedef enum {
    BG_REASON_BELOW_EIL,
    BG_REASON_SOIL_UNSAFE,
    BG_REASON_SPRAY,
    BG_REASON_MAX_SPRAYS,
    BG_REASON_MIN_GAP,
    BG_REASON_SOIL_FAULT,
    BG_REASON_CAMERA_FAULT,
    BG_REASON_LOW_BATTERY,
} bg_reason_t;

typedef struct {
    uint16_t n_pest;          /* N̂_pest from the aggregator */
    bool soil_safe;
    bool soil_fault;
    bool camera_fault;
    uint16_t batt_mv;
    uint8_t sprays_today;     /* from actuation lockout state */
    uint32_t min_since_last_spray; /* UINT32_MAX if never sprayed */
} bg_decision_in_t;

typedef struct {
    bg_action_t action;
    bg_reason_t reason;
} bg_decision_t;

bg_decision_t decision_evaluate(const bg_decision_in_t *in);

const char *decision_action_str(bg_action_t a);
const char *decision_reason_str(bg_reason_t r);
