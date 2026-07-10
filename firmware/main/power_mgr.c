/**
 * @file power_mgr.c
 * @brief Battery ADC + duty-cycled deep sleep.
 */
#include "power_mgr.h"
#include "app_config.h"

#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"

static const char *TAG = "power";

#define BATT_SAMPLES 8

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static uint16_t s_batt_mv;
static uint32_t s_cycle_min = BG_SLEEP_MIN_DEFAULT;

esp_err_t power_init(void)
{
    /* Duty-select jumper: input, pull-up; shorted to GND -> contingency. */
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BG_PIN_DUTY_SELECT,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) return err;
    s_cycle_min = gpio_get_level(BG_PIN_DUTY_SELECT) ? BG_SLEEP_MIN_DEFAULT
                                                     : BG_SLEEP_MIN_ALT;

    /* power_mgr owns ADC1; soil_sensor borrows the handle via
     * power_adc_unit(), so power_init() must run first (main.c does). */
    adc_oneshot_unit_init_cfg_t unit = { .unit_id = ADC_UNIT_1 };
    err = adc_oneshot_new_unit(&unit, &s_adc);
    if (err != ESP_OK) return err;

    adc_oneshot_chan_cfg_t chan = {
        .bitwidth = ADC_BITWIDTH_12,
        .atten = ADC_ATTEN_DB_12,
    };
    err = adc_oneshot_config_channel(s_adc, BG_ADC_CH_BATT, &chan);
    if (err != ESP_OK) return err;

    adc_cali_line_fitting_config_t cali = {
        .unit_id = ADC_UNIT_1,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    if (adc_cali_create_scheme_line_fitting(&cali, &s_cali) != ESP_OK) {
        s_cali = NULL;
    }

    /* Sample once at boot (before heavy loads switch on) for a clean OCV. */
    int64_t acc = 0;
    int valid_samples = 0;
    for (int i = 0; i < BATT_SAMPLES; i++) {
        int raw = 0, mv = 0;
        if (adc_oneshot_read(s_adc, BG_ADC_CH_BATT, &raw) != ESP_OK) continue;
        if (s_cali && adc_cali_raw_to_voltage(s_cali, raw, &mv) == ESP_OK) {
            acc += mv;
        } else {
            acc += raw * 3300 / 4095;
        }
        valid_samples++;
    }
    if (!valid_samples) {
        s_batt_mv = 0; /* fail safe: power_batt_critical() becomes true */
        ESP_LOGE(TAG, "battery ADC produced no valid samples");
    } else {
        s_batt_mv = (uint16_t)((acc / valid_samples) * BG_BATT_DIVIDER_NUM
                               / BG_BATT_DIVIDER_DEN);
    }

    ESP_LOGI(TAG, "battery %u mV, duty cycle %lu min",
             s_batt_mv, (unsigned long)s_cycle_min);
    return ESP_OK;
}

adc_oneshot_unit_handle_t power_adc_unit(void) { return s_adc; }

uint16_t power_batt_mv(void) { return s_batt_mv; }

bool power_batt_critical(void) { return s_batt_mv < BG_BATT_CRITICAL_MV; }

uint32_t power_cycle_minutes(void) { return s_cycle_min; }

void power_deep_sleep(int64_t t0_us)
{
    int64_t cycle_us = (int64_t)s_cycle_min * 60 * 1000000LL;
    int64_t awake_us = esp_timer_get_time() - t0_us;
    int64_t sleep_us = cycle_us - awake_us;
    /* Never sleep less than 10 s even if a cycle overran (e.g. long join). */
    if (sleep_us < 10 * 1000000LL) sleep_us = 10 * 1000000LL;

    /* Hold the relay pin OFF through sleep so the solenoid can't float on. */
    gpio_set_level(BG_PIN_RELAY, !BG_RELAY_ACTIVE_LEVEL);
    gpio_hold_en(BG_PIN_RELAY);
    gpio_deep_sleep_hold_en();

    ESP_LOGI(TAG, "deep sleep %lld s (awake %lld ms)",
             sleep_us / 1000000LL, awake_us / 1000LL);
    esp_sleep_enable_timer_wakeup((uint64_t)sleep_us);
    esp_deep_sleep_start();
}
