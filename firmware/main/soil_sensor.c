/**
 * @file soil_sensor.c
 * @brief Capacitive soil probe on ADC1 (default) or I2C sensor, with the
 *        Week-5 quadratic calibration applied in one place.
 */
#include "soil_sensor.h"
#include "app_config.h"
#include "power_mgr.h"

#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#if BG_SOIL_USE_I2C
#include "driver/i2c.h"
#endif

static const char *TAG = "soil";

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static bool s_ready;

esp_err_t soil_init(void)
{
#if BG_SOIL_USE_I2C
    /* Bus already installed by camera_init(); nothing else to set up. */
    s_ready = true;
    return ESP_OK;
#else
    /* ADC1 is owned by power_mgr (one oneshot owner per unit); borrow it.
     * Requires power_init() to have run first — main.c guarantees that. */
    s_adc = power_adc_unit();
    if (!s_adc) {
        ESP_LOGE(TAG, "power_init() must run before soil_init()");
        return ESP_ERR_INVALID_STATE;
    }

    adc_oneshot_chan_cfg_t chan = {
        .bitwidth = ADC_BITWIDTH_12,
        .atten = ADC_ATTEN_DB_12,   /* full 0-3.3 V probe range */
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, BG_ADC_CH_SOIL, &chan),
                        TAG, "chan cfg");

    adc_cali_line_fitting_config_t cali = {
        .unit_id = ADC_UNIT_1,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    if (adc_cali_create_scheme_line_fitting(&cali, &s_cali) != ESP_OK) {
        ESP_LOGW(TAG, "no eFuse cal — falling back to nominal transfer");
        s_cali = NULL;
    }
    s_ready = true;
    return ESP_OK;
#endif
}

static int read_probe_mv(void)
{
#if BG_SOIL_USE_I2C
    /* STEMMA-style: read 16-bit capacitance register 0x0F/0x10. */
    uint8_t cmd[2] = { 0x0F, 0x10 };
    uint8_t buf[2];
    if (i2c_master_write_read_device(I2C_NUM_0, BG_SOIL_I2C_ADDR, cmd, 2,
                                     buf, 2, pdMS_TO_TICKS(100)) != ESP_OK) {
        return -1;
    }
    return (buf[0] << 8) | buf[1];
#else
    int64_t acc = 0;
    for (int i = 0; i < BG_SOIL_SAMPLES; i++) {
        int raw = 0;
        if (adc_oneshot_read(s_adc, BG_ADC_CH_SOIL, &raw) != ESP_OK) return -1;
        int mv = 0;
        if (s_cali && adc_cali_raw_to_voltage(s_cali, raw, &mv) == ESP_OK) {
            acc += mv;
        } else {
            acc += raw * 3300 / 4095; /* nominal fallback */
        }
    }
    return (int)(acc / BG_SOIL_SAMPLES);
#endif
}

bg_soil_t soil_read(void)
{
    bg_soil_t out = { .fault = true, .soil_safe = false, .vwc_pct = 0, .raw_mv = -1 };
    if (!s_ready) {
        ESP_LOGE(TAG, "read before init");
        return out;
    }

    int mv = read_probe_mv();
    out.raw_mv = mv;
    if (mv < BG_SOIL_RAW_MIN_MV || mv > BG_SOIL_RAW_MAX_MV) {
        /* Disconnected probe reads near rail; short reads near 0. Either way
         * this is a sensor fault, not a moisture value. */
        ESP_LOGE(TAG, "raw %d mV outside [%d,%d] — sensor fault",
                 mv, BG_SOIL_RAW_MIN_MV, BG_SOIL_RAW_MAX_MV);
        return out;
    }

    /* Week-5 calibration: VWC% = A2*mv^2 + A1*mv + A0, clamped to [0,100]. */
    float vwc = BG_SOIL_CAL_A2 * (float)mv * (float)mv
              + BG_SOIL_CAL_A1 * (float)mv
              + BG_SOIL_CAL_A0;
    if (vwc < 0.f) vwc = 0.f;
    if (vwc > 100.f) vwc = 100.f;

    out.fault = false;
    out.vwc_pct = vwc;
    out.soil_safe = (vwc >= BG_SOIL_SAFE_MIN_PCT) && (vwc <= BG_SOIL_SAFE_MAX_PCT);
    ESP_LOGI(TAG, "%d mV -> VWC %.1f%% -> Soil_safe=%d", mv, vwc, out.soil_safe);
    return out;
}
