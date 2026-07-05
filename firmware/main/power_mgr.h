/**
 * @file power_mgr.h
 * @brief Deep-sleep duty-cycle scheduler + battery voltage sampling.
 *
 * Nominal cycle is BG_SLEEP_MIN_DEFAULT (30 min). Shorting the
 * BG_PIN_DUTY_SELECT jumper to GND at boot selects the Table III
 * contingency cycle of BG_SLEEP_MIN_ALT (45 min) for low-irradiance
 * deployments — checked once per wake, so the switch can be changed in
 * the field without reflashing.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_adc/adc_oneshot.h"
#include "esp_err.h"

esp_err_t power_init(void);

/** ADC1 oneshot unit handle — the oneshot driver allows only one owner per
 *  unit, so power_mgr owns ADC1 and soil_sensor borrows this handle. */
adc_oneshot_unit_handle_t power_adc_unit(void);

/** Battery pack millivolts (ADC through the divider, oversampled). */
uint16_t power_batt_mv(void);

/** True below BG_BATT_CRITICAL_MV: skip camera+inference this cycle. */
bool power_batt_critical(void);

/** Duty cycle chosen for THIS wake, in minutes (30 or 45). */
uint32_t power_cycle_minutes(void);

/** Sleep so that wake happens cycle_minutes after wake-up time `t0_us`
 *  (esp_timer epoch), compensating for time spent awake. Does not return. */
void power_deep_sleep(int64_t t0_us) __attribute__((noreturn));
