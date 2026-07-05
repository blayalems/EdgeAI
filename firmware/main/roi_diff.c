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

bg_roi_t roi_diff_detect(const uint8_t *thumb, int frame_w, int frame_h)
{
    bg_roi_t out = { 0 };

    if (s_ref_magic != REF_MAGIC) {
        /* Cold boot / power loss: seed reference, report no motion. */
        memcpy(s_ref, thumb, sizeof(s_ref));
        s_ref_magic = REF_MAGIC;
        ESP_LOGI(TAG, "reference seeded (cold boot)");
        return out;
    }

    int minx = BG_DIFF_THUMB_W, miny = BG_DIFF_THUMB_H, maxx = -1, maxy = -1;
    int active = 0;
    for (int y = 0; y < BG_DIFF_THUMB_H; y++) {
        for (int x = 0; x < BG_DIFF_THUMB_W; x++) {
            int i = y * BG_DIFF_THUMB_W + x;
            int d = (int)thumb[i] - (int)s_ref[i];
            if (d < 0) d = -d;
            if (d >= BG_DIFF_PIXEL_THRESHOLD) {
                active++;
                if (x < minx) minx = x;
                if (x > maxx) maxx = x;
                if (y < miny) miny = y;
                if (y > maxy) maxy = y;
            }
        }
    }

    out.active_pixels = active;
    if (active < BG_DIFF_MIN_ACTIVE_PIXELS) {
        ESP_LOGI(TAG, "no motion (%d active px)", active);
        return out;
    }

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

    /* Keep the crop square-ish so downscale to 96x96 doesn't squash the
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

    out.motion = true;
    out.x = fx; out.y = fy; out.w = fw; out.h = fh;
    ESP_LOGI(TAG, "motion: %d px, ROI %d,%d %dx%d", active, fx, fy, fw, fh);
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
