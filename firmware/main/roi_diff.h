/**
 * @file roi_diff.h
 * @brief Frame-differencing ROI module — the compute-saving stage.
 *
 * Compares the current frame's grayscale thumbnail against a reference
 * thumbnail persisted in RTC memory across deep-sleep cycles. Disconnected
 * changed-pixel components become separate padded crops, allowing the
 * classify-then-count pipeline to count multiple target instances in one
 * frame. If nothing moved, inference is skipped entirely.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "app_config.h"

typedef struct {
    bool motion;        /* true -> run inference on the crop below */
    int  active_pixels; /* changed-pixel count in the thumbnail (diagnostics) */
    int  x, y, w, h;    /* padded ROI in full-capture pixel coordinates */
} bg_roi_t;

/** Diff `thumb` (BG_DIFF_THUMB_W*H grayscale) against the RTC reference and
 *  return up to `capacity` disconnected moving regions, largest first.
 *  frame_w/frame_h are the full-capture dimensions the ROIs are mapped to.
 *  First boot seeds the reference and returns zero — one blind cycle instead
 *  of one false trigger. */
int roi_diff_detect_many(const uint8_t *thumb, int frame_w, int frame_h,
                         bg_roi_t *out, int capacity);

/** Compatibility helper returning only the largest moving region. */
bg_roi_t roi_diff_detect(const uint8_t *thumb, int frame_w, int frame_h);

/** Blend the current thumbnail into the reference (slow background update,
 *  alpha = BG_DIFF_REF_ALPHA_NUM/DEN). Call after detection each cycle so
 *  gradual lighting change doesn't accumulate into phantom motion. */
void roi_diff_update_reference(const uint8_t *thumb);
