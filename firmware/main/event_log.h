/**
 * @file event_log.h
 * @brief Flash-backed decision log + watchdog/recovery bookkeeping.
 *
 * Every wake cycle appends exactly one CSV record to SPIFFS — this file is
 * the reliability evidence for the evaluation (spray decisions can be
 * cross-checked against gateway telemetry). The logger also records why
 * the chip last reset (brownout, watchdog, panic, ...) so field failures
 * are diagnosable, and arms the task watchdog around the main loop.
 *
 * CSV schema (one line per wake):
 *   wake_no,uptime_ms,reset_reason,n_new,n_window,soil_mv,soil_vwc,
 *   soil_safe,batt_mv,cycle_min,action,reason
 */
#pragma once

#include <stdint.h>
#include "esp_err.h"

typedef struct {
    uint32_t wake_no;
    uint32_t uptime_ms;
    uint16_t n_new;
    uint16_t n_window;
    int      soil_mv;
    float    soil_vwc;
    uint8_t  soil_safe;
    uint16_t batt_mv;
    uint32_t cycle_min;
    const char *action;
    const char *reason;
} bg_log_rec_t;

/** Mount SPIFFS, rotate if oversized, log the reset reason, and subscribe
 *  the calling task to the task watchdog (BG_WDT_TIMEOUT_S). */
esp_err_t event_log_init(void);

/** Feed the watchdog — call between pipeline stages. */
void event_log_wdt_feed(void);

/** Append one decision record (also mirrored to the console). */
esp_err_t event_log_append(const bg_log_rec_t *rec);

/** Flush + unsubscribe from the WDT; call right before deep sleep. */
void event_log_close(void);
