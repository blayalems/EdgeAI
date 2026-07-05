/**
 * @file app_config.h
 * @brief BananaGuard node — SINGLE configuration header.
 *
 * Every tunable threshold, pin assignment, calibration coefficient and
 * timing constant for the node firmware lives in this file and ONLY in
 * this file. Modules must not hard-code magic numbers; if you need a new
 * knob, add it here with units in the name or a comment.
 */
#pragma once

#include <stdint.h>

/* ------------------------------------------------------------------ */
/* Identity / versioning                                               */
/* ------------------------------------------------------------------ */
#define BG_FW_VERSION_MAJOR        0
#define BG_FW_VERSION_MINOR        1

/* ------------------------------------------------------------------ */
/* Pin map (ESP32 DevKit / WROOM-32)                                   */
/* ------------------------------------------------------------------ */
/* ArduCAM Mini 5MP Plus (OV5642) — SPI for ArduChip/FIFO, I2C for sensor */
#define BG_PIN_CAM_SPI_SCLK        18
#define BG_PIN_CAM_SPI_MISO        19
#define BG_PIN_CAM_SPI_MOSI        23
#define BG_PIN_CAM_SPI_CS          5
#define BG_PIN_I2C_SDA             21   /* shared: camera sensor + soil (I2C mode) */
#define BG_PIN_I2C_SCL             22

/* LoRa radio (SX1276, e.g. RFM95W) */
#define BG_PIN_LORA_SPI_SCLK       14
#define BG_PIN_LORA_SPI_MISO       12
#define BG_PIN_LORA_SPI_MOSI       13
#define BG_PIN_LORA_CS             15
#define BG_PIN_LORA_RST            33
#define BG_PIN_LORA_DIO0           26
#define BG_PIN_LORA_DIO1           27

/* Actuation + sensing */
#define BG_PIN_RELAY               25   /* solenoid valve relay */
#define BG_RELAY_ACTIVE_LEVEL      1    /* 1 = active-high relay board */
#define BG_PIN_DUTY_SELECT         4    /* jumper: pulled-up; short to GND -> 45-min cycle */
#define BG_ADC_CH_SOIL             6    /* ADC1_CH6 = GPIO34 (capacitive probe, analog mode) */
#define BG_ADC_CH_BATT             7    /* ADC1_CH7 = GPIO35 via divider */

/* ------------------------------------------------------------------ */
/* Camera capture                                                      */
/* ------------------------------------------------------------------ */
#define BG_CAM_CAPTURE_W           320  /* fixed capture resolution (RGB565) */
#define BG_CAM_CAPTURE_H           240
#define BG_CAM_SPI_HZ              (4 * 1000 * 1000)
#define BG_CAM_CAPTURE_TIMEOUT_MS  3000
#define BG_CAM_WARMUP_FRAMES       2    /* discard frames while AEC settles */

/* ------------------------------------------------------------------ */
/* Frame-differencing ROI                                              */
/* ------------------------------------------------------------------ */
/* Diff runs on a grayscale thumbnail persisted in RTC memory across
 * deep-sleep cycles (keep W*H <= ~3 KB: RTC slow RAM is 8 KB total). */
#define BG_DIFF_THUMB_W            64
#define BG_DIFF_THUMB_H            48
#define BG_DIFF_PIXEL_THRESHOLD    28   /* |cur-ref| (0..255) for a pixel to count as "moving" */
#define BG_DIFF_MIN_ACTIVE_PIXELS  20   /* fewer than this in thumbnail -> no motion, skip inference */
#define BG_DIFF_ROI_PAD_PCT        15   /* pad bounding box by % of its size before cropping */
#define BG_DIFF_REF_ALPHA_NUM      1    /* ref = ref + (cur-ref) * NUM/DEN  (slow background update) */
#define BG_DIFF_REF_ALPHA_DEN      8

/* ------------------------------------------------------------------ */
/* Inference (TFLite-Micro, INT8 MobileNetV2)                          */
/* ------------------------------------------------------------------ */
#define BG_MODEL_INPUT_W           96
#define BG_MODEL_INPUT_H           96
#define BG_MODEL_INPUT_C           3
#define BG_TFLM_ARENA_KB           300  /* tensor arena; heap (PSRAM preferred) */
#define BG_CLASS_NEGATIVE          0    /* background / not-a-pest class index */
#define BG_CLASS_PEST              1    /* banana weevil class index */
#define BG_CONF_THRESHOLD_PCT      60   /* detections below this confidence are rejected */

/* ------------------------------------------------------------------ */
/* Detection aggregator (rolling window in RTC memory)                 */
/* ------------------------------------------------------------------ */
#define BG_AGG_WINDOW_MIN          30   /* rolling window for N̂_pest */
#define BG_AGG_BUCKET_SEC          300  /* 5-min buckets -> 6 buckets per 30-min window */
#define BG_AGG_BUCKET_COUNT        (BG_AGG_WINDOW_MIN * 60 / BG_AGG_BUCKET_SEC)

/* ------------------------------------------------------------------ */
/* Soil sensing + Week-5 calibration                                   */
/* ------------------------------------------------------------------ */
#define BG_SOIL_USE_I2C            0    /* 0 = analog capacitive probe on ADC, 1 = I2C sensor */
#define BG_SOIL_I2C_ADDR           0x36 /* (I2C mode) e.g. Adafruit STEMMA soil sensor */
#define BG_SOIL_SAMPLES            8    /* ADC oversampling */
/* Week-5 lab calibration: VWC% = A2*mv^2 + A1*mv + A0  (mv = probe millivolts).
 * Coefficients from the calibration fit; float math, applied in soil_sensor.c. */
#define BG_SOIL_CAL_A2             (-0.0000221f)
#define BG_SOIL_CAL_A1             (0.00195f)
#define BG_SOIL_CAL_A0             (108.0f)
/* Plausibility limits — readings outside are a sensor fault, not data. */
#define BG_SOIL_RAW_MIN_MV         200
#define BG_SOIL_RAW_MAX_MV         3100
/* Soil_safe band: spraying allowed only when VWC is inside this range
 * (too dry -> chemical stress; saturated -> runoff). */
#define BG_SOIL_SAFE_MIN_PCT       20.0f
#define BG_SOIL_SAFE_MAX_PCT       60.0f

/* ------------------------------------------------------------------ */
/* Decision engine — Eq. 2:  spray ⟺ (N̂_pest > N_EIL) ∧ Soil_safe      */
/* ------------------------------------------------------------------ */
#define BG_N_EIL                   5    /* economic injury level (detections / 30 min) */

/* ------------------------------------------------------------------ */
/* Actuation + hard safety lockout                                     */
/* ------------------------------------------------------------------ */
#define BG_SPRAY_PULSE_MS          3000 /* solenoid open time per spray event */
#define BG_MAX_SPRAYS_PER_DAY      4    /* hard lockout, persisted in RTC across sleep */
#define BG_SPRAY_MIN_GAP_MIN       30   /* min minutes between two sprays */
#define BG_BATT_MIN_SPRAY_MV       3500 /* don't actuate below this pack voltage */

/* ------------------------------------------------------------------ */
/* LoRaWAN telemetry                                                   */
/* ------------------------------------------------------------------ */
#define BG_LORA_REGION_US915       1
#define BG_LORA_SUBBAND            2    /* TTN US915 uses sub-band 2 (channels 8-15) */
#define BG_LORA_SF                 10   /* SF10 / 125 kHz per link-budget analysis */
#define BG_LORA_FPORT              1
#define BG_LORA_JOIN_TIMEOUT_S     30
#define BG_LORA_TX_TIMEOUT_S       15
/* OTAA credentials — provisioned per node; placeholders MUST be replaced.
 * (Keys in a header is acceptable for the prototype; production would use NVS.) */
#define BG_LORA_DEV_EUI            "0000000000000000"
#define BG_LORA_APP_EUI            "0000000000000000"
#define BG_LORA_APP_KEY            "00000000000000000000000000000000"

/* ------------------------------------------------------------------ */
/* Power manager                                                       */
/* ------------------------------------------------------------------ */
#define BG_SLEEP_MIN_DEFAULT       30   /* nominal duty cycle */
#define BG_SLEEP_MIN_ALT           45   /* Table III contingency (jumper on BG_PIN_DUTY_SELECT) */
#define BG_BATT_DIVIDER_NUM        2    /* Vbatt = adc_mv * NUM / DEN  (2:1 divider) */
#define BG_BATT_DIVIDER_DEN        1
#define BG_BATT_CRITICAL_MV        3300 /* below this: skip camera+inference, uplink & sleep */

/* ------------------------------------------------------------------ */
/* Event logger + watchdog                                             */
/* ------------------------------------------------------------------ */
#define BG_LOG_MOUNT_POINT         "/storage"
#define BG_LOG_FILE                BG_LOG_MOUNT_POINT "/decisions.csv"
#define BG_LOG_FILE_OLD            BG_LOG_MOUNT_POINT "/decisions.old.csv"
#define BG_LOG_MAX_BYTES           (256 * 1024) /* rotate at this size */
#define BG_WDT_TIMEOUT_S           30   /* task watchdog; must cover one camera capture */
