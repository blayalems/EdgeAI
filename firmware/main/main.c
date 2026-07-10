/**
 * @file main.c
 * @brief BananaGuard node — one duty cycle per boot.
 *
 * The node lives in deep sleep and this file is the whole life of one wake:
 *
 *   boot -> watchdog+log -> power/battery -> aggregator restore
 *        -> capture -> frame-diff ROI -> (motion?) INT8 inference
 *        -> aggregate N̂_pest -> soil read -> decision (Eq. 2)
 *        -> (spray?) actuate with lockout -> LoRa uplink -> log -> sleep
 *
 * Every stage that can fail degrades instead of aborting: a camera fault
 * still senses soil and uplinks; a soil fault still uplinks; a radio fault
 * still logs. Only the decision path is strict: any sensor fault forbids
 * spraying (decision_engine + actuation both enforce it).
 */
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"

#include "app_config.h"
#include "actuation.h"
#include "camera_capture.h"
#include "decision_engine.h"
#include "detection_agg.h"
#include "event_log.h"
#include "inference.h"
#include "lora_telemetry.h"
#include "power_mgr.h"
#include "roi_diff.h"
#include "soil_sensor.h"

static const char *TAG = "main";

void app_main(void)
{
    const int64_t t0 = esp_timer_get_time();

    /* --- infrastructure --- */
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        err = nvs_flash_erase();
        if (err == ESP_OK) err = nvs_flash_init();
    }
    const bool nvs_ready = (err == ESP_OK);
    if (!nvs_ready) {
        ESP_LOGE(TAG, "NVS init failed: %s; LoRaWAN disabled", esp_err_to_name(err));
    }
    err = event_log_init();    /* watchdog armed from here on */
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "event log init failed: %s", esp_err_to_name(err));
    }
    const esp_err_t actuation_init_err = actuation_init();
    const bool actuation_ready = (actuation_init_err == ESP_OK);
    if (!actuation_ready) {
        ESP_LOGE(TAG, "actuation init failed: %s", esp_err_to_name(actuation_init_err));
    }
    const esp_err_t power_init_err = power_init(); /* owns ADC1 for soil */
    const bool power_ready = (power_init_err == ESP_OK);
    if (!power_ready) {
        ESP_LOGE(TAG, "power init failed: %s", esp_err_to_name(power_init_err));
    }
    agg_init();

    /* --- sensing + inference pipeline --- */
    bool camera_fault = !power_ready;
    bool camera_initialized = false;
    bool infer_ready = false;
    uint16_t n_new = 0;

    if (!power_ready) {
        ESP_LOGE(TAG, "power subsystem unavailable — skipping camera cycle");
        camera_fault = true;
    } else if (power_batt_critical()) {
        /* Not enough energy for the imaging pipeline; report and sleep. */
        ESP_LOGW(TAG, "battery critical (%u mV) — skipping camera cycle",
                 power_batt_mv());
        /* This is a deliberate power lockout, not a camera failure. The
         * decision engine and actuator both block low-voltage spraying. */
        camera_fault = false;
    } else {
        camera_initialized = (camera_init() == ESP_OK);
        camera_fault = !camera_initialized;
        infer_ready = camera_initialized && (inference_init() == ESP_OK);
        if (camera_initialized && !infer_ready) {
            /* Classifier initialization is part of the camera pipeline's
             * trust boundary. Historical counts must not actuate when the
             * current cycle cannot classify. */
            camera_fault = true;
        }

        if (camera_initialized && infer_ready) {
            bg_frame_t frame = { 0 };
            if (camera_capture(&frame) == ESP_OK) {
                event_log_wdt_feed();

                static uint8_t thumb[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];
                camera_thumbnail_gray(&frame, thumb);
                bg_roi_t rois[BG_DIFF_MAX_ROIS];
                int roi_count = roi_diff_detect_many(thumb, frame.w, frame.h,
                                                      rois, BG_DIFF_MAX_ROIS);

                if (roi_count > 0 && infer_ready) {
                    static uint8_t input[BG_MODEL_INPUT_W * BG_MODEL_INPUT_H *
                                         BG_MODEL_INPUT_C];
                    for (int i = 0; i < roi_count; i++) {
                        camera_downscale_rgb888(&frame, rois[i].x, rois[i].y,
                                                rois[i].w, rois[i].h, input,
                                                BG_MODEL_INPUT_W,
                                                BG_MODEL_INPUT_H);
                        event_log_wdt_feed();

                        bg_inference_result_t res;
                        if (inference_run(input, &res) != ESP_OK) {
                            /* The payload has no separate inference-fault bit;
                             * report it as a camera-pipeline fault so historical
                             * window counts cannot actuate after a failed run. */
                            infer_ready = false;
                            camera_fault = true;
                            break;
                        }
                        if (res.pest && n_new < UINT16_MAX) n_new++;
                    }
                }
                /* Update reference AFTER detection so this cycle's subject
                 * bleeds into the background only slowly. */
                roi_diff_update_reference(thumb);
                camera_frame_free(&frame);
            } else {
                camera_fault = true;
            }
        }
        if (camera_initialized) camera_power_down();
    }
    event_log_wdt_feed();

    agg_add_detections(n_new);
    const uint16_t n_pest = agg_window_count();

    /* --- soil --- */
    bg_soil_t soil = {
        .fault = true,
        .soil_safe = false,
        .vwc_pct = 0.f,
        .raw_mv = -1,
    };
    if (power_ready) {
        esp_err_t soil_init_err = soil_init();
        if (soil_init_err == ESP_OK) {
            soil = soil_read();
        } else {
            ESP_LOGE(TAG, "soil init failed: %s", esp_err_to_name(soil_init_err));
        }
    }

    /* --- decision (Eq. 2) --- */
    const bg_decision_in_t din = {
        .n_pest = n_pest,
        .soil_safe = soil.soil_safe,
        .soil_fault = soil.fault,
        .camera_fault = camera_fault,
        .actuator_fault = !actuation_ready,
        .batt_mv = power_batt_mv(),
        .sprays_today = actuation_ready ? actuation_sprays_today() : 0,
        .min_since_last_spray = actuation_ready
                               ? actuation_min_since_last() : 0,
    };
    bg_decision_t d = decision_evaluate(&din);
    ESP_LOGI(TAG, "decision: %s (%s), N̂_pest=%u vs EIL=%d, Soil_safe=%d",
             decision_action_str(d.action), decision_reason_str(d.reason),
             n_pest, BG_N_EIL, soil.soil_safe);

    /* --- actuation --- */
    if (d.action == BG_ACTION_SPRAY) {
        esp_err_t spray_err = actuation_spray(soil.fault || camera_fault,
                                              soil.soil_safe,
                                              power_batt_mv());
        if (spray_err != ESP_OK) {
            ESP_LOGE(TAG, "actuation failed/refused: %s", esp_err_to_name(spray_err));
            d.action = (spray_err == ESP_ERR_INVALID_STATE)
                     ? BG_ACTION_LOCKOUT : BG_ACTION_FAULT;
            d.reason = (spray_err == ESP_ERR_INVALID_STATE)
                     ? BG_REASON_ACTUATION_REFUSED : BG_REASON_ACTUATION_FAULT;
        }
        event_log_wdt_feed();
    }

    /* --- telemetry --- */
    const bg_uplink_t up = {
        .n_pest = n_pest,
        .soil_safe = soil.soil_safe,
        .soil_fault = soil.fault,
        .camera_fault = camera_fault,
        .infer_ready = infer_ready,
        .lockout_active = (d.action == BG_ACTION_LOCKOUT),
        .soil_vwc_pct = soil.fault ? 0xFF : (uint8_t)(soil.vwc_pct + 0.5f),
        .batt_mv = power_batt_mv(),
        .action = (uint8_t)d.action,
        .sprays_today = actuation_ready ? actuation_sprays_today() : 0,
    };
    if (nvs_ready && lora_init() == ESP_OK) {
        lora_send(&up);
    }
    event_log_wdt_feed();

    /* --- persist evidence, then sleep --- */
    const bg_log_rec_t rec = {
        .wake_no = agg_wake_count(),
        .uptime_ms = (uint32_t)((esp_timer_get_time() - t0) / 1000),
        .n_new = n_new,
        .n_window = n_pest,
        .soil_mv = soil.raw_mv,
        .soil_vwc = soil.vwc_pct,
        .soil_safe = soil.soil_safe,
        .batt_mv = power_batt_mv(),
        .cycle_min = power_cycle_minutes(),
        .action = decision_action_str(d.action),
        .reason = decision_reason_str(d.reason),
    };
    event_log_append(&rec);
    event_log_close();

    power_deep_sleep(t0); /* does not return */
}
