/**
 * @file detection_agg.c
 * @brief RTC-memory rolling detection counter. See header for the spec.
 */
#include <stddef.h>
#include <string.h>
#include <time.h>

#include "detection_agg.h"
#include "app_config.h"
#include "esp_attr.h"
#include "esp_crc.h"
#include "esp_log.h"

static const char *TAG = "agg";

#define AGG_MAGIC 0xA6600B57u

typedef struct {
    uint32_t magic;
    uint32_t wake_count;
    struct {
        uint32_t bucket_idx;   /* unix_seconds / BG_AGG_BUCKET_SEC; 0 = empty */
        uint16_t count;
    } bucket[BG_AGG_BUCKET_COUNT];
    uint32_t crc;              /* CRC32 over everything above */
} agg_state_t;

static RTC_DATA_ATTR agg_state_t s_agg;

static uint32_t calc_crc(const agg_state_t *s)
{
    return esp_crc32_le(0, (const uint8_t *)s, offsetof(agg_state_t, crc));
}

static void seal(void) { s_agg.crc = calc_crc(&s_agg); }

static uint32_t now_bucket(void)
{
    return (uint32_t)(time(NULL) / BG_AGG_BUCKET_SEC);
}

static void expire_old(void)
{
    uint32_t nb = now_bucket();
    uint32_t oldest_valid = (nb >= BG_AGG_BUCKET_COUNT - 1)
                          ? nb - (BG_AGG_BUCKET_COUNT - 1) : 0;
    for (int i = 0; i < BG_AGG_BUCKET_COUNT; i++) {
        if (s_agg.bucket[i].bucket_idx &&
            s_agg.bucket[i].bucket_idx < oldest_valid) {
            s_agg.bucket[i].bucket_idx = 0;
            s_agg.bucket[i].count = 0;
        }
    }
}

void agg_init(void)
{
    if (s_agg.magic != AGG_MAGIC || s_agg.crc != calc_crc(&s_agg)) {
        ESP_LOGW(TAG, "RTC state invalid (cold boot or corruption) — counter reset");
        memset(&s_agg, 0, sizeof(s_agg));
        s_agg.magic = AGG_MAGIC;
        seal();
    }
    s_agg.wake_count++;
    expire_old();
    seal();
    ESP_LOGI(TAG, "wake #%lu, N̂_pest(window)=%u",
             (unsigned long)s_agg.wake_count, agg_window_count());
}

void agg_add_detections(uint16_t n)
{
    if (!n) return;
    uint32_t nb = now_bucket();
    expire_old();

    /* Find current bucket, else reuse an empty slot, else evict the oldest. */
    int slot = -1, oldest = 0;
    for (int i = 0; i < BG_AGG_BUCKET_COUNT; i++) {
        if (s_agg.bucket[i].bucket_idx == nb) { slot = i; break; }
        if (s_agg.bucket[i].bucket_idx == 0 && slot < 0) slot = i;
        if (s_agg.bucket[i].bucket_idx < s_agg.bucket[oldest].bucket_idx)
            oldest = i;
    }
    if (slot < 0) {
        slot = oldest;
        s_agg.bucket[slot].count = 0;
    }
    s_agg.bucket[slot].bucket_idx = nb;
    uint32_t sum = s_agg.bucket[slot].count + n;
    s_agg.bucket[slot].count = sum > UINT16_MAX ? UINT16_MAX : (uint16_t)sum;
    seal();
}

uint16_t agg_window_count(void)
{
    uint32_t total = 0;
    for (int i = 0; i < BG_AGG_BUCKET_COUNT; i++) {
        total += s_agg.bucket[i].count;
    }
    return total > UINT16_MAX ? UINT16_MAX : (uint16_t)total;
}

uint32_t agg_wake_count(void)
{
    return s_agg.wake_count;
}
