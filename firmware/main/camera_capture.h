/**
 * @file camera_capture.h
 * @brief ArduCAM-style 5MP SPI camera driver (OV5642 behind ArduChip).
 *
 * The OV5640/OV5642 sensor itself is a DVP/MIPI part with no SPI interface,
 * so the node uses an ArduCAM Mini 5MP Plus: the on-board ArduChip exposes a
 * frame FIFO over SPI while sensor registers are programmed over I2C.
 *
 * Pipeline responsibilities of this module:
 *   - capture one frame at the fixed resolution BG_CAM_CAPTURE_W/H (RGB565),
 *   - box-downscale any rectangular crop of it to the model input size,
 *   - produce the grayscale thumbnail used by the frame-differencing module.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "app_config.h"

typedef struct {
    uint16_t *rgb565;   /* BG_CAM_CAPTURE_W * BG_CAM_CAPTURE_H pixels, heap-owned */
    int w;
    int h;
} bg_frame_t;

/** Probe ArduChip (SPI test register) + sensor (I2C chip ID), program QVGA
 *  RGB565 capture mode. Returns ESP_FAIL on wiring/ID mismatch -> sensor fault. */
esp_err_t camera_init(void);

/** Capture one frame into an internally allocated buffer.
 *  Discards BG_CAM_WARMUP_FRAMES first so auto-exposure settles. */
esp_err_t camera_capture(bg_frame_t *out);

/** Free a frame returned by camera_capture(). */
void camera_frame_free(bg_frame_t *f);

/** Box-average downscale of crop (x,y,w,h) of `src` into an RGB888 buffer
 *  dst[dw*dh*3]. Used to build the model input from an ROI crop. */
void camera_downscale_rgb888(const bg_frame_t *src,
                             int x, int y, int w, int h,
                             uint8_t *dst, int dw, int dh);

/** Full-frame grayscale thumbnail (BG_DIFF_THUMB_W x BG_DIFF_THUMB_H) for the
 *  frame-differencing module. dst must hold W*H bytes. */
void camera_thumbnail_gray(const bg_frame_t *src, uint8_t *dst);

/** Power the camera down before deep sleep (ArduChip standby + sensor PWDN). */
void camera_power_down(void);
