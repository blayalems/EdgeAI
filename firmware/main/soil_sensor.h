/**
 * @file soil_sensor.h
 * @brief Soil moisture sensing: ADC (capacitive probe) or I2C sensor,
 *        Week-5 calibration curve, binary Soil_safe output.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct {
    bool  fault;      /* read failed or out of plausible range — do NOT spray */
    bool  soil_safe;  /* VWC inside [BG_SOIL_SAFE_MIN_PCT, BG_SOIL_SAFE_MAX_PCT] */
    float vwc_pct;    /* calibrated volumetric water content, % */
    int   raw_mv;     /* probe millivolts (ADC mode) / raw counts (I2C mode) */
} bg_soil_t;

esp_err_t soil_init(void);

/** Oversampled read + calibration + safety-band evaluation.
 *  Never returns garbage as data: implausible raw values set .fault. */
bg_soil_t soil_read(void);
