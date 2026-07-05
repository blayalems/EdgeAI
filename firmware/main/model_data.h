/**
 * @file model_data.h
 * @brief INT8 MobileNetV2 model, linked into the app as a C array.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

extern const unsigned char g_model_data[];
extern const size_t g_model_data_len;

#ifdef __cplusplus
}
#endif
