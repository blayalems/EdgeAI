/**
 * @file inference.h
 * @brief TFLite-Micro wrapper for the INT8 MobileNetV2 pest classifier.
 *
 * C API over the C++ runtime (inference.cc). The model is linked in as a
 * C array (model_data.cc, generated with `xxd -i` from the quantized
 * .tflite). Returns class + confidence; the negative/background class is
 * rejected and every other manuscript target class qualifies as a pest.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "app_config.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool    pest;            /* true for any non-negative class above confidence threshold */
    uint8_t class_id;        /* argmax class index */
    uint8_t confidence_pct;  /* softmax confidence of argmax, 0..100 */
} bg_inference_result_t;

/** Map the model, build the interpreter, allocate the tensor arena
 *  (heap, PSRAM preferred). ESP_ERR_INVALID_STATE if the linked model
 *  array is still the placeholder. */
esp_err_t inference_init(void);

/** Classify one BG_MODEL_INPUT_W x H x C RGB888 image.
 *  Quantizes uint8 -> int8 per the input tensor's zero point. */
esp_err_t inference_run(const uint8_t *rgb888, bg_inference_result_t *out);

#ifdef __cplusplus
}
#endif
