/**
 * @file roi_diff.c
 * @brief Frame differencing against an RTC-persisted reference thumbnail.
 *
 * RTC slow RAM is 8 KB total; the 64x48 grayscale reference costs 3 KB and
 * survives deep sleep (but not power loss — handled by the magic word).
 */
#include <string.h>

#include "roi_diff.h"
#include "esp_attr.h"
#include "esp_log.h"

static const char *TAG = "roi";

#define REF_MAGIC 0xB60D1FF0u

static RTC_DATA_ATTR uint32_t s_ref_magic;
static RTC_DATA_ATTR uint8_t  s_ref[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];

/* Scratch memory is ordinary DRAM: only the reference belongs in scarce RTC
 * memory. The module is called synchronously once per wake, so static scratch
 * buffers are safe and avoid consuming the main task's stack. */
static uint8_t  s_changed[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];
static uint8_t  s_seen[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];
static uint16_t s_queue[BG_DIFF_THUMB_W * BG_DIFF_THUMB_H];

static bg_roi_t map_component(int minx, int miny, int maxx, int maxy,
                              int component_pixels, int frame_w, int frame_h)
{
    bg_roi_t out = {
        .motion = true,
        .active_pixels = component_pixels,
    };

    /* Map thumbnail bbox -> full-frame pixels, then pad. */
    int fx = minx * frame_w / BG_DIFF_THUMB_W;
    int fy = miny * frame_h / BG_DIFF_THUMB_H;
    int fw = (maxx - minx + 1) * frame_w / BG_DIFF_THUMB_W;
    int fh = (maxy - miny + 1) * frame_h / BG_DIFF_THUMB_H;

    int px = fw * BG_DIFF_ROI_PAD_PCT / 100;
    int py = fh * BG_DIFF_ROI_PAD_PCT / 100;
    fx -= px; fy -= py; fw += 2 * px; fh += 2 * py;

    if (fx < 0) { fw += fx; fx = 0; }
    if (fy < 0) { fh += fy; fy = 0; }
    if (fx + fw > frame_w) fw = frame_w - fx;
    if (fy + fh > frame_h) fh = frame_h - fy;

    /* Keep the crop square-ish so downscale to 96x96 does not squash the
     * subject: grow the short side within frame bounds. */
    if (fw < fh) {
        int grow = fh - fw;
        fx -= grow / 2;
        if (fx < 0) fx = 0;
        fw = (fx + fh <= frame_w) ? fh : frame_w - fx;
    } else if (fh < fw) {
        int grow = fw - fh;
        fy -= grow / 2;
        if (fy < 0) fy = 0;
        fh = (fy + fw <= frame_h) ? fw : frame_h - fy;
    }

    out.x = fx; out.y = fy; out.w = fw; out.h = fh;
    return out;
}

static void retain_largest(bg_roi_t *out, int capacity, int *kept,
                           bg_roi_t candidate)
{
    if (*kept < capacity) {
        out[(*kept)++] = candidate;
        return;
    }

    int smallest = 0;
    for (int i = 1; i < *kept; i++) {
        if (out[i].active_pixels < out[smallest].active_pixels) smallest = i;
    }
    if (candidate.active_pixels > out[smallest].active_pixels) {
        out[smallest] = candidate;
    }
}

int roi_diff_detect_many(const uint8_t *thumb, int frame_w, int frame_h,
                         bg_roi_t *out, int capacity)
{
    if (!thumb || !out || capacity <= 0 || frame_w <= 0 || frame_h <= 0) {
        return 0;
    }

    if (s_ref_magic != REF_MAGIC) {
        /* Cold boot / power loss: seed reference, report no motion. */
        memcpy(s_ref, thumb, sizeof(s_ref));
        s_ref_magic = REF_MAGIC;
        ESP_LOGI(TAG, "reference seeded (cold boot)");
        return 0;
    }

    int active = 0;
    for (int i = 0; i < (int)sizeof(s_changed); i++) {
        int d = (int)thumb[i] - (int)s_ref[i];
        if (d < 0) d = -d;
        s_changed[i] = (d >= BG_DIFF_PIXEL_THRESHOLD);
        active += s_changed[i] ? 1 : 0;
    }

    if (active < BG_DIFF_MIN_ACTIVE_PIXELS) {
        ESP_LOGI(TAG, "no motion (%d active px)", active);
        return 0;
    }

    memset(s_seen, 0, sizeof(s_seen));
    int kept = 0;
    for (int seed = 0; seed < (int)sizeof(s_changed); seed++) {
        if (!s_changed[seed] || s_seen[seed]) continue;

        int head = 0, tail = 0;
        s_queue[tail++] = (uint16_t)seed;
        s_seen[seed] = 1;
        int minx = BG_DIFF_THUMB_W, miny = BG_DIFF_THUMB_H;
        int maxx = -1, maxy = -1, component_pixels = 0;

        while (head < tail) {
            int pos = s_queue[head++];
            int x = pos % BG_DIFF_THUMB_W;
            int y = pos / BG_DIFF_THUMB_W;
            component_pixels++;
            if (x < minx) minx = x;
            if (x > maxx) maxx = x;
            if (y < miny) miny = y;
            if (y > maxy) maxy = y;

            /* Eight-connected components keep diagonal portions of one
             * moving subject together. */
            for (int dy = -1; dy <= 1; dy++) {
                for (int dx = -1; dx <= 1; dx++) {
                    if (!dx && !dy) continue;
                    int nx = x + dx, ny = y + dy;
                    if (nx < 0 || nx >= BG_DIFF_THUMB_W ||
                        ny < 0 || ny >= BG_DIFF_THUMB_H) continue;
                    int next = ny * BG_DIFF_THUMB_W + nx;
                    if (s_changed[next] && !s_seen[next]) {
                        s_seen[next] = 1;
                        s_queue[tail++] = (uint16_t)next;
                    }
                }
            }
        }

        if (component_pixels >= BG_DIFF_MIN_COMPONENT_PIXELS) {
            retain_largest(out, capacity, &kept,
                           map_component(minx, miny, maxx, maxy,
                                         component_pixels, frame_w, frame_h));
        }
    }

    /* Largest first so the most informative crops are classified before a
     * watchdog or power constraint can cut a cycle short. */
    for (int i = 0; i < kept; i++) {
        for (int j = i + 1; j < kept; j++) {
            if (out[j].active_pixels > out[i].active_pixels) {
                bg_roi_t tmp = out[i]; out[i] = out[j]; out[j] = tmp;
            }
        }
    }

    ESP_LOGI(TAG, "motion: %d active px -> %d ROI(s)", active, kept);
    return kept;
}

bg_roi_t roi_diff_detect(const uint8_t *thumb, int frame_w, int frame_h)
{
    bg_roi_t out = { 0 };
    roi_diff_detect_many(thumb, frame_w, frame_h, &out, 1);
    return out;
}

void roi_diff_update_reference(const uint8_t *thumb)
{
    if (s_ref_magic != REF_MAGIC) return;
    for (int i = 0; i < (int)sizeof(s_ref); i++) {
        int delta = (int)thumb[i] - (int)s_ref[i];
        s_ref[i] = (uint8_t)((int)s_ref[i] +
                             delta * BG_DIFF_REF_ALPHA_NUM / BG_DIFF_REF_ALPHA_DEN);
    }
}
