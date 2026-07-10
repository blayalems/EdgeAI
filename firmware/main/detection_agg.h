/**
 * @file detection_agg.h
 * @brief Rolling 30-min N̂_pest counter, persisted in RTC memory across
 *        deep-sleep cycles.
 *
 * Explicit spec (this is the part that's easy to get wrong):
 *
 *  - The window is BG_AGG_WINDOW_MIN minutes, quantized into
 *    BG_AGG_BUCKET_COUNT buckets of BG_AGG_BUCKET_SEC seconds.
 *  - Each bucket stores {valid, bucket_index, count} where bucket_index =
 *    unix_seconds / BG_AGG_BUCKET_SEC. The ESP32 RTC keeps the system
 *    clock running through deep sleep, so bucket indices stay comparable
 *    across wake cycles without an external RTC.
 *  - State lives in RTC_DATA_ATTR memory guarded by MAGIC + CRC32:
 *      * deep-sleep wake  -> magic+CRC valid -> state carries over;
 *      * power loss/brownout/flash of new fw -> guard fails -> counter
 *        resets to 0 (after a cold boot, only post-boot classifications can
 *        contribute to a spray decision).
 *  - On every add or read (including a zero-detection wake), buckets whose
 *    index is older than
 *    (now_bucket - BG_AGG_BUCKET_COUNT + 1) are expired to zero, so
 *    N̂_pest is always the sum over exactly the trailing window even if
 *    the node overslept (e.g. 45-min contingency cycle).
 *  - CRC is recomputed after every mutation, not at sleep time, so a
 *    crash between add and sleep can't persist a torn state.
 */
#pragma once

#include <stdint.h>

/** Validate RTC state; reset it (with a log line) if the guard fails. */
void agg_init(void);

/** Record `n` pest detections at the current time. */
void agg_add_detections(uint16_t n);

/** N̂_pest: total detections in the trailing BG_AGG_WINDOW_MIN minutes. */
uint16_t agg_window_count(void);

/** Monotonic wake-cycle counter (diagnostics + telemetry sequence). */
uint32_t agg_wake_count(void);
