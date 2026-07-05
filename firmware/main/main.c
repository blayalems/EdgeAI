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
        nvs_flash_erase();
        nvs_flash_init();
    }
    event_log_init();          /* watchdog armed from here on */
    power_init();              /* must precede soil_init (ADC1 owner) */
    actuation_init();          /* relay driven OFF as early as possible */
    agg_init();

    /* --- sensing + inference pipeline --- */
    bool camera_fault = false;
    bool infer_ready = false;
    uint16_t n_new = 0;

    if (power_batt_critical()) {
        /* Not enough energy for the imaging pipeline; report and sleep. */
        ESP_LOGW(TAG, "battery critical (%u mV) — skipping camera cycle",
                 power_batt_mv());
        camera_fault = true; /* conservatively forbid spraying too */
    } else {
        camera_fault = (camera_init() != ESP_OK);
        infer_ready = !camera_fault && (inference_init() == ESP_OK);

        if (!camera_fault) {
            bg_frame_t frame = { 0 };
            if (camera_capture(&frame) == ESP_OK) {
                event_log_wdt_feed();

                static uint8_t thumb[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];
                camera_thumbnail_gray(&frame, thumb);
                bg_roi_t roi = roi_diff_detect(thumb, frame.w, frame.h);

                if (roi.motion && infer_ready) {
                    static uint8_t input[BG_MODEL_INPUT_W * BG_MODEL_INPUT_H *
                                         BG_MODEL_INPUT_C];
                    camera_downscale_rgb888(&frame, roi.x, roi.y, roi.w, roi.h,
                                            input, BG_MODEL_INPUT_W,
                                            BG_MODEL_INPUT_H);
                    event_log_wdt_feed();

                    bg_inference_result_t res;
                    if (inference_run(input, &res) == ESP_OK && res.pest) {
                        n_new = 1;
                    }
                }
                /* Update reference AFTER detection so this cycle's subject
                 * bleeds into the background only slowly. */
                roi_diff_update_reference(thumb);
                camera_frame_free(&frame);
            } else {
                camera_fault = true;
            }
            camera_power_down();
        }
    }
    event_log_wdt_feed();

    agg_add_detections(n_new);
    const uint16_t n_pest = agg_window_count();

    /* --- soil --- */
    soil_init();
    const bg_soil_t soil = soil_read();

    /* --- decision (Eq. 2) --- */
    const bg_decision_in_t din = {
        .n_pest = n_pest,
        .soil_safe = soil.soil_safe,
        .soil_fault = soil.fault,
        .camera_fault = camera_fault,
        .batt_mv = power_batt_mv(),
        .sprays_today = actuation_sprays_today(),
        .min_since_last_spray = actuation_min_since_last(),
    };
    const bg_decision_t d = decision_evaluate(&din);
    ESP_LOGI(TAG, "decision: %s (%s), N̂_pest=%u vs EIL=%d, Soil_safe=%d",
             decision_action_str(d.action), decision_reason_str(d.reason),
             n_pest, BG_N_EIL, soil.soil_safe);

    /* --- actuation --- */
    if (d.action == BG_ACTION_SPRAY) {
        if (actuation_spray(soil.fault || camera_fault) != ESP_OK) {
            ESP_LOGE(TAG, "actuation refused by hard lockout");
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
        .sprays_today = actuation_sprays_today(),
    };
    if (lora_init() == ESP_OK) {
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
