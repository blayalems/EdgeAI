/**
 * @file actuation.c
 * @brief Relay GPIO pulse + RTC-persisted daily spray lockout.
 */
#include <stddef.h>
#include <time.h>

#include "actuation.h"
#include "app_config.h"

#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_crc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "act";

#define ACT_MAGIC 0xACC7100u

typedef struct {
    uint32_t magic;
    uint32_t day_idx;        /* unix_seconds / 86400 the counter belongs to */
    uint8_t  sprays_today;
    uint8_t  has_last_spray; /* explicit: unix timestamp zero is valid */
    uint32_t last_spray_ts;  /* unix seconds */
    uint32_t crc;
} act_state_t;

static RTC_DATA_ATTR act_state_t s_act;

static uint32_t calc_crc(void)
{
    return esp_crc32_le(0, (const uint8_t *)&s_act, offsetof(act_state_t, crc));
}

static esp_err_t relay_off(void)
{
    return gpio_set_level(BG_PIN_RELAY, !BG_RELAY_ACTIVE_LEVEL);
}

static void roll_day(void)
{
    uint32_t today = (uint32_t)(time(NULL) / 86400);
    if (s_act.day_idx != today) {
        s_act.day_idx = today;
        s_act.sprays_today = 0;
        s_act.crc = calc_crc();
    }
}

esp_err_t actuation_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BG_PIN_RELAY,
        .mode = GPIO_MODE_OUTPUT,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) return err;

    /* Program the safe latch while the deep-sleep hold is still active, then
     * release it. Without this release, a wake from deep sleep can leave the
     * relay permanently held OFF and make every reported spray a no-op. */
    err = relay_off();
    if (err != ESP_OK) return err;
    gpio_deep_sleep_hold_dis();
    err = gpio_hold_dis(BG_PIN_RELAY);
    if (err != ESP_OK) return err;
    err = relay_off();
    if (err != ESP_OK) return err;

    if (s_act.magic != ACT_MAGIC || s_act.crc != calc_crc()) {
        ESP_LOGW(TAG, "lockout state invalid (cold boot) — reset");
        s_act = (act_state_t){ .magic = ACT_MAGIC };
        s_act.crc = calc_crc();
    }
    roll_day();
    ESP_LOGI(TAG, "sprays today: %u/%u", s_act.sprays_today, BG_MAX_SPRAYS_PER_DAY);
    return ESP_OK;
}

uint8_t actuation_sprays_today(void)
{
    roll_day();
    return s_act.sprays_today;
}

uint32_t actuation_min_since_last(void)
{
    if (!s_act.has_last_spray) return UINT32_MAX;
    time_t now = time(NULL);
    if ((uint32_t)now <= s_act.last_spray_ts) return 0;
    return ((uint32_t)now - s_act.last_spray_ts) / 60;
}

esp_err_t actuation_spray(bool sensor_fault, bool soil_safe, uint16_t batt_mv)
{
    /* Hard lockout re-check — this driver is the last line of defence and
     * must not trust that the caller already ran the decision engine. */
    roll_day();
    if (sensor_fault) {
        ESP_LOGE(TAG, "REFUSED: sensor fault");
        return ESP_ERR_INVALID_STATE;
    }
    if (!soil_safe) {
        ESP_LOGE(TAG, "REFUSED: soil unsafe");
        return ESP_ERR_INVALID_STATE;
    }
    if (batt_mv < BG_BATT_MIN_SPRAY_MV) {
        ESP_LOGE(TAG, "REFUSED: battery %u mV < %u mV",
                 batt_mv, BG_BATT_MIN_SPRAY_MV);
        return ESP_ERR_INVALID_STATE;
    }
    if (s_act.sprays_today >= BG_MAX_SPRAYS_PER_DAY) {
        ESP_LOGE(TAG, "REFUSED: daily limit (%u)", s_act.sprays_today);
        return ESP_ERR_INVALID_STATE;
    }
    if (actuation_min_since_last() < BG_SPRAY_MIN_GAP_MIN) {
        ESP_LOGE(TAG, "REFUSED: %lu min since last spray < %d",
                 (unsigned long)actuation_min_since_last(), BG_SPRAY_MIN_GAP_MIN);
        return ESP_ERR_INVALID_STATE;
    }

    /* Commit the lockout BEFORE energizing: if we brown out mid-pulse the
     * conservative outcome (spray counted, maybe not fully delivered) is
     * the safe one. */
    s_act.sprays_today++;
    s_act.has_last_spray = 1;
    s_act.last_spray_ts = (uint32_t)time(NULL);
    s_act.crc = calc_crc();

    ESP_LOGI(TAG, "solenoid ON for %d ms (spray %u/%u today)",
             BG_SPRAY_PULSE_MS, s_act.sprays_today, BG_MAX_SPRAYS_PER_DAY);
    esp_err_t err = gpio_set_level(BG_PIN_RELAY, BG_RELAY_ACTIVE_LEVEL);
    if (err != ESP_OK) {
        relay_off();
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(BG_SPRAY_PULSE_MS));
    err = relay_off();
    if (err != ESP_OK) return err;
    ESP_LOGI(TAG, "solenoid OFF");
    return ESP_OK;
}
