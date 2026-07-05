/**
 * @file model_data.cc
 * @brief PLACEHOLDER for the trained INT8 MobileNetV2 .tflite model.
 *
 * Regenerate after Week-6 training freeze:
 *
 *   xxd -i pest_mnv2_int8.tflite > model_data_gen.inc
 *
 * then paste the array below (keep the alignment attribute — TFLite-Micro
 * requires 16-byte alignment) and update g_model_data_len.
 *
 * inference_init() detects this placeholder (len < 1024) and reports
 * ESP_ERR_INVALID_STATE so the rest of the firmware still runs: the node
 * boots, senses soil, uplinks and sleeps — it just logs INFER_FAULT
 * instead of classifying. That keeps hardware bring-up unblocked while the
 * model is still training.
 */
#include "model_data.h"

alignas(16) const unsigned char g_model_data[] = {
    /* placeholder — NOT a valid flatbuffer */
    0x00, 0x00, 0x00, 0x00,
};
const size_t g_model_data_len = sizeof(g_model_data);
