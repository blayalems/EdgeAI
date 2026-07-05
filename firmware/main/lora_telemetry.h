/**
 * @file lora_telemetry.h
 * @brief LoRaWAN uplink: OTAA join, US915 sub-band 2, SF10, compact binary
 *        payload.
 *
 * Uplink payload v1 — 9 bytes, big-endian, FPort BG_LORA_FPORT:
 *
 *   offset  size  field
 *   0       1     payload version (0x01)
 *   1       2     N̂_pest rolling-window count (uint16)
 *   3       1     flags: bit0 Soil_safe, bit1 soil_fault, bit2 camera_fault,
 *                        bit3 infer_ready, bit4 lockout_active
 *   4       1     soil VWC, % (0-100, 0xFF = fault/invalid)
 *   5       2     battery millivolts (uint16)
 *   7       1     action code (bg_action_t)
 *   8       1     sprays_today
 *
 * At SF10/125 kHz a 9-byte FRMPayload keeps time-on-air ≈ 290 ms, inside
 * duty-cycle and TTN fair-use budgets at 30-min cycles.
 *
 * Implementation: uses the ttn-esp32 component when present (detected via
 * __has_include("ttn.h")); otherwise compiles a stub that logs the exact
 * bytes it would send, so the rest of the firmware is testable without a
 * gateway. Join state is kept by the LoRaWAN stack in NVS, so OTAA happens
 * once, not every wake cycle.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct {
    uint16_t n_pest;
    bool soil_safe;
    bool soil_fault;
    bool camera_fault;
    bool infer_ready;
    bool lockout_active;
    uint8_t soil_vwc_pct;   /* 0xFF = invalid */
    uint16_t batt_mv;
    uint8_t action;         /* bg_action_t */
    uint8_t sprays_today;
} bg_uplink_t;

/** Radio + stack init, OTAA join (skipped if session persisted in NVS). */
esp_err_t lora_init(void);

/** Encode + transmit one unconfirmed uplink. Returns ESP_OK if handed to
 *  the radio; telemetry failure never blocks the decision path. */
esp_err_t lora_send(const bg_uplink_t *u);

/** Serialize the payload (exposed separately for host-side unit tests). */
int lora_encode(const bg_uplink_t *u, uint8_t *buf, int cap);
