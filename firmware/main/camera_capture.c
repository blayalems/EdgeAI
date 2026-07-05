/**
 * @file camera_capture.c
 * @brief ArduCAM Mini 5MP Plus (OV5642 + ArduChip) capture over SPI/I2C.
 *
 * ArduChip protocol (SPI, mode 0):
 *   - write reg:  {addr | 0x80, value}
 *   - read  reg:  {addr & 0x7F, dummy} -> second byte is value
 *   - burst FIFO read: 0x3C then clock out N bytes
 *
 * NOTE (bring-up): the OV5642 sensor init table below is a trimmed QVGA
 * RGB565 sequence. Validate against ArduCAM's reference ov5642_regs.h during
 * Week-4 bring-up; the ArduChip/FIFO logic in this file does not change.
 */
#include <stdlib.h>
#include <string.h>

#include "camera_capture.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "driver/i2c.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "camera";

/* ---- ArduChip registers ---- */
#define ARDUCHIP_TEST1        0x00
#define ARDUCHIP_TEST_VAL     0x55
#define ARDUCHIP_FIFO         0x04
#define FIFO_CLEAR_MASK       0x01
#define FIFO_START_MASK       0x02
#define ARDUCHIP_GPIO         0x06
#define GPIO_PWDN_MASK        0x02
#define ARDUCHIP_TRIG         0x41
#define CAP_DONE_MASK         0x08
#define FIFO_SIZE1            0x42
#define FIFO_SIZE2            0x43
#define FIFO_SIZE3            0x44
#define BURST_FIFO_READ       0x3C

/* ---- OV5642 over I2C (SCCB) ---- */
#define OV5642_I2C_ADDR       0x3C
#define OV5642_CHIPID_HIGH    0x300A
#define OV5642_CHIPID_LOW     0x300B
#define OV5642_ID             0x5642

#define I2C_PORT              I2C_NUM_0
#define I2C_TIMEOUT_MS        100

static spi_device_handle_t s_spi;
static bool s_ready;

/* Trimmed OV5642 init: software reset, DVP RGB565 output, QVGA timing.
 * See file header note about validating against the vendor table. */
typedef struct { uint16_t reg; uint8_t val; } sensor_reg_t;
static const sensor_reg_t ov5642_qvga_rgb565[] = {
    {0x3103, 0x93}, {0x3008, 0x82}, /* soft reset */
    {0x3017, 0x7f}, {0x3018, 0xfc}, /* DVP pins enabled */
    {0x3810, 0xc2}, {0x3615, 0xf0},
    {0x4300, 0x61},                 /* RGB565 output format */
    {0x501f, 0x01},                 /* ISP -> RGB */
    {0x3808, 0x01}, {0x3809, 0x40}, /* out width  = 320 */
    {0x380a, 0x00}, {0x380b, 0xf0}, /* out height = 240 */
    {0x5001, 0x7f},                 /* scaling on */
    {0x3503, 0x00},                 /* AEC/AGC auto */
    {0xffff, 0xff},                 /* end marker */
};

/* ---------------- low-level helpers ---------------- */

static uint8_t chip_read(uint8_t addr)
{
    uint8_t tx[2] = { (uint8_t)(addr & 0x7F), 0x00 };
    uint8_t rx[2] = { 0 };
    spi_transaction_t t = {
        .length = 16, .tx_buffer = tx, .rx_buffer = rx,
    };
    spi_device_polling_transmit(s_spi, &t);
    return rx[1];
}

static void chip_write(uint8_t addr, uint8_t val)
{
    uint8_t tx[2] = { (uint8_t)(addr | 0x80), val };
    spi_transaction_t t = { .length = 16, .tx_buffer = tx };
    spi_device_polling_transmit(s_spi, &t);
}

static esp_err_t sensor_write(uint16_t reg, uint8_t val)
{
    uint8_t buf[3] = { (uint8_t)(reg >> 8), (uint8_t)reg, val };
    return i2c_master_write_to_device(I2C_PORT, OV5642_I2C_ADDR, buf, sizeof(buf),
                                      pdMS_TO_TICKS(I2C_TIMEOUT_MS));
}

static esp_err_t sensor_read(uint16_t reg, uint8_t *val)
{
    uint8_t a[2] = { (uint8_t)(reg >> 8), (uint8_t)reg };
    return i2c_master_write_read_device(I2C_PORT, OV5642_I2C_ADDR, a, 2, val, 1,
                                        pdMS_TO_TICKS(I2C_TIMEOUT_MS));
}

/* ---------------- public API ---------------- */

esp_err_t camera_init(void)
{
    /* SPI bus for ArduChip */
    spi_bus_config_t bus = {
        .sclk_io_num = BG_PIN_CAM_SPI_SCLK,
        .miso_io_num = BG_PIN_CAM_SPI_MISO,
        .mosi_io_num = BG_PIN_CAM_SPI_MOSI,
        .quadwp_io_num = -1, .quadhd_io_num = -1,
        .max_transfer_sz = 4096,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;

    spi_device_interface_config_t dev = {
        .clock_speed_hz = BG_CAM_SPI_HZ,
        .mode = 0,
        .spics_io_num = BG_PIN_CAM_SPI_CS,
        .queue_size = 2,
    };
    ESP_RETURN_ON_ERROR(spi_bus_add_device(SPI2_HOST, &dev, &s_spi), TAG, "spi dev");

    /* I2C bus for the sensor (shared with I2C soil sensor if enabled) */
    i2c_config_t i2c = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = BG_PIN_I2C_SDA,
        .scl_io_num = BG_PIN_I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 100000,
    };
    i2c_param_config(I2C_PORT, &i2c);
    err = i2c_driver_install(I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;

    /* Probe ArduChip: test register must read back what we wrote. */
    chip_write(ARDUCHIP_TEST1, ARDUCHIP_TEST_VAL);
    if (chip_read(ARDUCHIP_TEST1) != ARDUCHIP_TEST_VAL) {
        ESP_LOGE(TAG, "ArduChip SPI probe failed (wiring/CS?)");
        return ESP_FAIL;
    }

    /* Wake sensor from PWDN, probe chip ID. */
    chip_write(ARDUCHIP_GPIO, 0x00);
    vTaskDelay(pdMS_TO_TICKS(5));
    uint8_t idh = 0, idl = 0;
    if (sensor_read(OV5642_CHIPID_HIGH, &idh) != ESP_OK ||
        sensor_read(OV5642_CHIPID_LOW, &idl) != ESP_OK ||
        ((idh << 8) | idl) != OV5642_ID) {
        ESP_LOGE(TAG, "OV5642 ID mismatch: %02x%02x", idh, idl);
        return ESP_FAIL;
    }

    for (const sensor_reg_t *r = ov5642_qvga_rgb565; r->reg != 0xffff; r++) {
        ESP_RETURN_ON_ERROR(sensor_write(r->reg, r->val), TAG, "sensor cfg");
        if (r->val == 0x82 && r->reg == 0x3008) vTaskDelay(pdMS_TO_TICKS(10));
    }
    vTaskDelay(pdMS_TO_TICKS(50)); /* let AEC start converging */

    s_ready = true;
    ESP_LOGI(TAG, "camera ready (%dx%d RGB565)", BG_CAM_CAPTURE_W, BG_CAM_CAPTURE_H);
    return ESP_OK;
}

static esp_err_t capture_once(uint16_t *dst)
{
    chip_write(ARDUCHIP_FIFO, FIFO_CLEAR_MASK);
    chip_write(ARDUCHIP_FIFO, FIFO_START_MASK);

    int64_t deadline = esp_timer_get_time() + (int64_t)BG_CAM_CAPTURE_TIMEOUT_MS * 1000;
    while (!(chip_read(ARDUCHIP_TRIG) & CAP_DONE_MASK)) {
        if (esp_timer_get_time() > deadline) {
            ESP_LOGE(TAG, "capture timeout");
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(1);
    }

    uint32_t len = chip_read(FIFO_SIZE1) |
                   (chip_read(FIFO_SIZE2) << 8) |
                   ((chip_read(FIFO_SIZE3) & 0x7F) << 16);
    const uint32_t expect = BG_CAM_CAPTURE_W * BG_CAM_CAPTURE_H * 2;
    if (len < expect) {
        ESP_LOGE(TAG, "short FIFO: %lu < %lu", (unsigned long)len, (unsigned long)expect);
        return ESP_FAIL;
    }

    /* Burst-read the FIFO in DMA-sized chunks straight into dst. */
    uint8_t cmd = BURST_FIFO_READ;
    spi_device_acquire_bus(s_spi, portMAX_DELAY);
    spi_transaction_t t0 = { .length = 8, .tx_buffer = &cmd, .flags = SPI_TRANS_CS_KEEP_ACTIVE };
    spi_device_polling_transmit(s_spi, &t0);

    uint8_t *p = (uint8_t *)dst;
    uint32_t remaining = expect;
    while (remaining) {
        uint32_t chunk = remaining > 4096 ? 4096 : remaining;
        spi_transaction_t t = {
            .length = chunk * 8,
            .rx_buffer = p,
            .flags = (remaining - chunk) ? SPI_TRANS_CS_KEEP_ACTIVE : 0,
        };
        esp_err_t err = spi_device_polling_transmit(s_spi, &t);
        if (err != ESP_OK) {
            spi_device_release_bus(s_spi);
            return err;
        }
        p += chunk;
        remaining -= chunk;
    }
    spi_device_release_bus(s_spi);

    /* ArduChip streams big-endian RGB565; ESP32 is little-endian. */
    for (uint32_t i = 0; i < expect / 2; i++) {
        dst[i] = (uint16_t)((dst[i] >> 8) | (dst[i] << 8));
    }
    return ESP_OK;
}

esp_err_t camera_capture(bg_frame_t *out)
{
    if (!s_ready) return ESP_ERR_INVALID_STATE;

    size_t bytes = BG_CAM_CAPTURE_W * BG_CAM_CAPTURE_H * sizeof(uint16_t);
    uint16_t *buf = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) buf = heap_caps_malloc(bytes, MALLOC_CAP_8BIT);
    if (!buf) return ESP_ERR_NO_MEM;

    for (int i = 0; i < BG_CAM_WARMUP_FRAMES; i++) {
        esp_err_t err = capture_once(buf);
        if (err != ESP_OK) { free(buf); return err; }
    }
    esp_err_t err = capture_once(buf);
    if (err != ESP_OK) { free(buf); return err; }

    out->rgb565 = buf;
    out->w = BG_CAM_CAPTURE_W;
    out->h = BG_CAM_CAPTURE_H;
    return ESP_OK;
}

void camera_frame_free(bg_frame_t *f)
{
    if (f && f->rgb565) { free(f->rgb565); f->rgb565 = NULL; }
}

static inline void rgb565_to_888(uint16_t px, uint8_t *r, uint8_t *g, uint8_t *b)
{
    *r = (uint8_t)(((px >> 11) & 0x1F) << 3);
    *g = (uint8_t)(((px >> 5) & 0x3F) << 2);
    *b = (uint8_t)((px & 0x1F) << 3);
}

void camera_downscale_rgb888(const bg_frame_t *src,
                             int x, int y, int w, int h,
                             uint8_t *dst, int dw, int dh)
{
    /* Box average: each output pixel averages its source cell. Integer-only. */
    for (int oy = 0; oy < dh; oy++) {
        int sy0 = y + oy * h / dh;
        int sy1 = y + (oy + 1) * h / dh;
        if (sy1 <= sy0) sy1 = sy0 + 1;
        for (int ox = 0; ox < dw; ox++) {
            int sx0 = x + ox * w / dw;
            int sx1 = x + (ox + 1) * w / dw;
            if (sx1 <= sx0) sx1 = sx0 + 1;
            uint32_t rs = 0, gs = 0, bs = 0, n = 0;
            for (int sy = sy0; sy < sy1 && sy < src->h; sy++) {
                for (int sx = sx0; sx < sx1 && sx < src->w; sx++) {
                    uint8_t r, g, b;
                    rgb565_to_888(src->rgb565[sy * src->w + sx], &r, &g, &b);
                    rs += r; gs += g; bs += b; n++;
                }
            }
            if (!n) n = 1;
            uint8_t *o = &dst[(oy * dw + ox) * 3];
            o[0] = (uint8_t)(rs / n);
            o[1] = (uint8_t)(gs / n);
            o[2] = (uint8_t)(bs / n);
        }
    }
}

void camera_thumbnail_gray(const bg_frame_t *src, uint8_t *dst)
{
    for (int oy = 0; oy < BG_DIFF_THUMB_H; oy++) {
        int sy0 = oy * src->h / BG_DIFF_THUMB_H;
        int sy1 = (oy + 1) * src->h / BG_DIFF_THUMB_H;
        for (int ox = 0; ox < BG_DIFF_THUMB_W; ox++) {
            int sx0 = ox * src->w / BG_DIFF_THUMB_W;
            int sx1 = (ox + 1) * src->w / BG_DIFF_THUMB_W;
            uint32_t sum = 0, n = 0;
            for (int sy = sy0; sy < sy1; sy++) {
                for (int sx = sx0; sx < sx1; sx++) {
                    uint8_t r, g, b;
                    rgb565_to_888(src->rgb565[sy * src->w + sx], &r, &g, &b);
                    /* luma approx: (2R + 5G + B) / 8 */
                    sum += (2u * r + 5u * g + b) >> 3;
                    n++;
                }
            }
            dst[oy * BG_DIFF_THUMB_W + ox] = (uint8_t)(n ? sum / n : 0);
        }
    }
}

void camera_power_down(void)
{
    if (!s_ready) return;
    chip_write(ARDUCHIP_GPIO, GPIO_PWDN_MASK); /* assert sensor PWDN */
}
