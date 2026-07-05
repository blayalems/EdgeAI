/**
 * @file actuation.h
 * @brief Relay/solenoid driver with a hard, RTC-persisted safety lockout.
 *
 * Lockout invariants (enforced HERE, independently of the decision engine,
 * so no caller bug can over-spray):
 *   - at most BG_MAX_SPRAYS_PER_DAY actuations per calendar day,
 *   - at least BG_SPRAY_MIN_GAP_MIN minutes between actuations,
 *   - refuse if a sensor fault flag is passed in,
 *   - relay is actively driven to OFF at init and after every pulse,
 *     and defaults OFF through deep sleep (gpio hold).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t actuation_init(void);

/** Timed solenoid pulse (BG_SPRAY_PULSE_MS). Re-checks the lockout
 *  internally; returns ESP_ERR_INVALID_STATE if refused. */
esp_err_t actuation_spray(bool sensor_fault);

/** Lockout state for the decision engine / telemetry. */
uint8_t  actuation_sprays_today(void);
uint32_t actuation_min_since_last(void); /* UINT32_MAX if never */
