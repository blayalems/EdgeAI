/**
 * @file inference.cc
 * @brief TFLite-Micro runtime hosting the INT8 MobileNetV2 classifier.
 */
#include "inference.h"

#include <cmath>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "model_data.h"

#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

static const char *TAG = "infer";

namespace {
const tflite::Model *s_model;
tflite::MicroInterpreter *s_interpreter;
uint8_t *s_arena;

/* Op set for quantized MobileNetV2 (+softmax head). */
using PestOpResolver = tflite::MicroMutableOpResolver<10>;
PestOpResolver *s_resolver;
}  // namespace

extern "C" esp_err_t inference_init(void)
{
    if (s_interpreter) return ESP_OK;
    if (g_model_data_len < 1024) {
        ESP_LOGE(TAG, "model_data.cc is still the placeholder (%u bytes) — "
                      "regenerate with `xxd -i` from the trained INT8 .tflite",
                 (unsigned)g_model_data_len);
        return ESP_ERR_INVALID_STATE;
    }

    s_model = tflite::GetModel(g_model_data);
    if (s_model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "schema version %lu != %d",
                 (unsigned long)s_model->version(), TFLITE_SCHEMA_VERSION);
        return ESP_ERR_INVALID_VERSION;
    }

    static PestOpResolver resolver;
    resolver.AddConv2D();
    resolver.AddDepthwiseConv2D();
    resolver.AddAdd();
    resolver.AddPad();
    resolver.AddMean();
    resolver.AddFullyConnected();
    resolver.AddReshape();
    resolver.AddSoftmax();
    resolver.AddAveragePool2D();
    resolver.AddQuantize();
    s_resolver = &resolver;

    const size_t arena_bytes = (size_t)BG_TFLM_ARENA_KB * 1024;
    s_arena = (uint8_t *)heap_caps_malloc(arena_bytes,
                                          MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_arena) {
        s_arena = (uint8_t *)heap_caps_malloc(arena_bytes, MALLOC_CAP_8BIT);
    }
    if (!s_arena) {
        ESP_LOGE(TAG, "no memory for %u KB tensor arena", BG_TFLM_ARENA_KB);
        return ESP_ERR_NO_MEM;
    }

    static tflite::MicroInterpreter interpreter(s_model, *s_resolver,
                                                s_arena, arena_bytes);
    if (interpreter.AllocateTensors() != kTfLiteOk) {
        ESP_LOGE(TAG, "AllocateTensors failed — arena too small?");
        return ESP_FAIL;
    }
    TfLiteTensor *in = interpreter.input(0);
    TfLiteTensor *out = interpreter.output(0);
    if (in->dims->size != 4 ||
        in->dims->data[1] != BG_MODEL_INPUT_H ||
        in->dims->data[2] != BG_MODEL_INPUT_W ||
        in->dims->data[3] != BG_MODEL_INPUT_C ||
        in->type != kTfLiteInt8) {
        ESP_LOGE(TAG, "model input shape/type mismatch with app_config.h");
        return ESP_ERR_INVALID_SIZE;
    }
    if (out->dims->size < 1 ||
        out->dims->data[out->dims->size - 1] != BG_MODEL_CLASS_COUNT ||
        out->type != kTfLiteInt8) {
        ESP_LOGE(TAG, "model output must be INT8 with %d classes",
                 BG_MODEL_CLASS_COUNT);
        return ESP_ERR_INVALID_SIZE;
    }
    if (in->params.scale <= 0.f || out->params.scale <= 0.f) {
        ESP_LOGE(TAG, "model has invalid quantization scale");
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "model loaded: %u bytes, arena used %u/%u KB",
             (unsigned)g_model_data_len,
             (unsigned)(interpreter.arena_used_bytes() / 1024),
             BG_TFLM_ARENA_KB);
    s_interpreter = &interpreter;
    return ESP_OK;
}

extern "C" esp_err_t inference_run(const uint8_t *rgb888,
                                   bg_inference_result_t *out)
{
    if (!s_interpreter) return ESP_ERR_INVALID_STATE;

    TfLiteTensor *in = s_interpreter->input(0);
    const int n = BG_MODEL_INPUT_W * BG_MODEL_INPUT_H * BG_MODEL_INPUT_C;
    /* The converter is calibrated in the raw 0..255 pixel domain. Apply the
     * tensor's actual affine quantizer instead of assuming scale == 1:
     * q = round(pixel / scale) + zero_point. */
    if (in->params.scale <= 0.f) return ESP_ERR_INVALID_STATE;
    const float inv_scale = 1.f / in->params.scale;
    const int32_t zp = in->params.zero_point;
    for (int i = 0; i < n; i++) {
        int32_t q = (int32_t)std::round((float)rgb888[i] * inv_scale) + zp;
        if (q < -128) q = -128;
        if (q > 127) q = 127;
        in->data.int8[i] = (int8_t)q;
    }

    const int64_t t0 = esp_timer_get_time();
    if (s_interpreter->Invoke() != kTfLiteOk) {
        ESP_LOGE(TAG, "Invoke failed");
        return ESP_FAIL;
    }
    /* Machine-parseable timing line — consumed by ml/device_latency.py to
     * retire the Week-4 "is the C6 fast enough" risk. Do not reformat. */
    ESP_LOGI(TAG, "invoke_us=%lld", (long long)(esp_timer_get_time() - t0));

    TfLiteTensor *o = s_interpreter->output(0);
    const int classes = o->dims->data[o->dims->size - 1];
    int best = 0;
    int8_t best_q = o->data.int8[0];
    for (int c = 1; c < classes; c++) {
        if (o->data.int8[c] > best_q) { best_q = o->data.int8[c]; best = c; }
    }
    /* Dequantize argmax score to a percentage. */
    float conf = (best_q - o->params.zero_point) * o->params.scale;
    if (conf < 0.f) conf = 0.f;
    if (conf > 1.f) conf = 1.f;

    out->class_id = (uint8_t)best;
    out->confidence_pct = (uint8_t)(conf * 100.f + 0.5f);
    out->pest = (best != BG_CLASS_NEGATIVE) &&
                (out->confidence_pct >= BG_CONF_THRESHOLD_PCT);

    ESP_LOGI(TAG, "class=%d conf=%u%% -> %s", best, out->confidence_pct,
             out->pest ? "PEST" : "rejected");
    return ESP_OK;
}
