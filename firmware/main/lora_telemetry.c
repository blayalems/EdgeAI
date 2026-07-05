/**
 * @file lora_telemetry.c
 * @brief Payload encoding + OTAA/uplink via ttn-esp32 (stub fallback).
 */
#include <stdio.h>
#include <string.h>

#include "lora_telemetry.h"
#include "app_config.h"
#include "esp_log.h"

static const char *TAG = "lora";

#define BG_UPLINK_VERSION 0x01
#define BG_UPLINK_LEN     9

int lora_encode(const bg_uplink_t *u, uint8_t *buf, int cap)
{
    if (cap < BG_UPLINK_LEN) return -1;
    buf[0] = BG_UPLINK_VERSION;
    buf[1] = (uint8_t)(u->n_pest >> 8);
    buf[2] = (uint8_t)(u->n_pest);
    buf[3] = (uint8_t)((u->soil_safe      ? 1u << 0 : 0) |
                       (u->soil_fault     ? 1u << 1 : 0) |
                       (u->camera_fault   ? 1u << 2 : 0) |
                       (u->infer_ready    ? 1u << 3 : 0) |
                       (u->lockout_active ? 1u << 4 : 0));
    buf[4] = u->soil_vwc_pct;
    buf[5] = (uint8_t)(u->batt_mv >> 8);
    buf[6] = (uint8_t)(u->batt_mv);
    buf[7] = u->action;
    buf[8] = u->sprays_today;
    return BG_UPLINK_LEN;
}

#if defined(__has_include)
#if __has_include("ttn.h")
#define BG_HAVE_TTN 1
#endif
#endif

#ifdef BG_HAVE_TTN
/* ------------------------------------------------------------------ */
/* Real stack: ttn-esp32 (LoRaMAC-node port) driving an SX1276.        */
/* ------------------------------------------------------------------ */
#include "ttn.h"
#include "driver/spi_common.h"

esp_err_t lora_init(void)
{
    spi_bus_config_t bus = {
        .sclk_io_num = BG_PIN_LORA_SPI_SCLK,
        .miso_io_num = BG_PIN_LORA_SPI_MISO,
        .mosi_io_num = BG_PIN_LORA_SPI_MOSI,
        .quadwp_io_num = -1, .quadhd_io_num = -1,
    };
    esp_err_t err = spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;

    ttn_init();
    ttn_configure_pins(SPI3_HOST, BG_PIN_LORA_CS, TTN_NOT_CONNECTED,
                       BG_PIN_LORA_RST, BG_PIN_LORA_DIO0, BG_PIN_LORA_DIO1);
#if BG_LORA_REGION_US915
    ttn_set_subband(BG_LORA_SUBBAND);
#endif
    /* SF10 <=> DR0 in US915 uplink table. */
    ttn_set_data_rate(TTN_DR_US915_SF10);
    ttn_set_adr_enabled(false);

    if (!ttn_resume_after_deep_sleep()) {
        ttn_provision(BG_LORA_DEV_EUI, BG_LORA_APP_EUI, BG_LORA_APP_KEY);
        ESP_LOGI(TAG, "OTAA join (timeout %d s)...", BG_LORA_JOIN_TIMEOUT_S);
        if (!ttn_join()) {
            ESP_LOGE(TAG, "join failed");
            return ESP_FAIL;
        }
    }
    ESP_LOGI(TAG, "LoRaWAN session active");
    return ESP_OK;
}

esp_err_t lora_send(const bg_uplink_t *u)
{
    uint8_t buf[BG_UPLINK_LEN];
    int len = lora_encode(u, buf, sizeof(buf));
    if (len < 0) return ESP_ERR_INVALID_SIZE;

    ttn_response_code_t rc = ttn_transmit_message(buf, len, BG_LORA_FPORT, false);
    if (rc != TTN_SUCCESSFUL_TRANSMISSION) {
        ESP_LOGE(TAG, "uplink failed (%d)", rc);
        return ESP_FAIL;
    }
    ttn_prepare_for_deep_sleep();
    ESP_LOGI(TAG, "uplink sent (%d bytes, SF%d)", len, BG_LORA_SF);
    return ESP_OK;
}

#else
/* ------------------------------------------------------------------ */
/* Stub: no ttn-esp32 component in the build. Logs the exact payload   */
/* so integration tests can proceed without a gateway.                 */
/* ------------------------------------------------------------------ */
esp_err_t lora_init(void)
{
    ESP_LOGW(TAG, "ttn-esp32 not in build — telemetry stubbed "
                  "(clone it into firmware/components/, see README)");
    return ESP_OK;
}

esp_err_t lora_send(const bg_uplink_t *u)
{
    uint8_t buf[BG_UPLINK_LEN];
    int len = lora_encode(u, buf, sizeof(buf));
    if (len < 0) return ESP_ERR_INVALID_SIZE;
    char hex[BG_UPLINK_LEN * 2 + 1];
    for (int i = 0; i < len; i++) sprintf(&hex[i * 2], "%02X", buf[i]);
    ESP_LOGI(TAG, "[stub] uplink fport=%d sf=%d payload=%s",
             BG_LORA_FPORT, BG_LORA_SF, hex);
    return ESP_OK;
}
#endif
