/**
 * @file event_log.c
 * @brief SPIFFS CSV decision log, reset-reason forensics, task watchdog.
 */
#include <stdbool.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

#include "event_log.h"
#include "app_config.h"

#include "esp_log.h"
#include "esp_spiffs.h"
#include "esp_system.h"
#include "esp_task_wdt.h"

static const char *TAG = "elog";

static bool s_mounted;

static const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON:   return "poweron";
    case ESP_RST_SW:        return "sw_reset";
    case ESP_RST_PANIC:     return "panic";
    case ESP_RST_INT_WDT:   return "int_wdt";
    case ESP_RST_TASK_WDT:  return "task_wdt";
    case ESP_RST_WDT:       return "other_wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep_wake";
    case ESP_RST_BROWNOUT:  return "brownout";
    default:                return "unknown";
    }
}

static void rotate_if_needed(void)
{
    struct stat st;
    if (stat(BG_LOG_FILE, &st) == 0 && st.st_size > BG_LOG_MAX_BYTES) {
        unlink(BG_LOG_FILE_OLD);
        rename(BG_LOG_FILE, BG_LOG_FILE_OLD);
        ESP_LOGI(TAG, "rotated log (%ld bytes)", (long)st.st_size);
    }
}

esp_err_t event_log_init(void)
{
    /* Watchdog first: it must cover the mount itself. */
    esp_task_wdt_config_t wdt = {
        .timeout_ms = BG_WDT_TIMEOUT_S * 1000,
        .trigger_panic = true, /* panic -> reset -> logged next boot */
    };
    esp_err_t err = esp_task_wdt_init(&wdt);
    if (err == ESP_ERR_INVALID_STATE) {
        esp_task_wdt_reconfigure(&wdt); /* already inited by the system */
    }
    esp_task_wdt_add(NULL);

    esp_vfs_spiffs_conf_t conf = {
        .base_path = BG_LOG_MOUNT_POINT,
        .partition_label = "storage",
        .max_files = 3,
        .format_if_mount_failed = true, /* first boot / corruption recovery */
    };
    err = esp_vfs_spiffs_register(&conf);
    if (err != ESP_OK) {
        /* Logging must never brick the node: continue console-only. */
        ESP_LOGE(TAG, "SPIFFS mount failed (%s) — console-only logging",
                 esp_err_to_name(err));
        s_mounted = false;
    } else {
        s_mounted = true;
        rotate_if_needed();
    }

    esp_reset_reason_t rr = esp_reset_reason();
    ESP_LOGI(TAG, "reset reason: %s", reset_reason_str(rr));
    if (s_mounted && rr != ESP_RST_DEEPSLEEP && rr != ESP_RST_POWERON) {
        /* Abnormal reset (crash/brownout/WDT): leave a forensic marker. */
        FILE *f = fopen(BG_LOG_FILE, "a");
        if (f) {
            fprintf(f, "#RECOVERY,%s\n", reset_reason_str(rr));
            fclose(f);
        }
    }
    return ESP_OK;
}

void event_log_wdt_feed(void)
{
    esp_task_wdt_reset();
}

esp_err_t event_log_append(const bg_log_rec_t *r)
{
    ESP_LOGI(TAG,
             "DECISION wake=%lu n_new=%u n_win=%u soil=%.1f%%(safe=%u) "
             "batt=%umV cycle=%lumin -> %s (%s)",
             (unsigned long)r->wake_no, r->n_new, r->n_window, r->soil_vwc,
             r->soil_safe, r->batt_mv, (unsigned long)r->cycle_min,
             r->action, r->reason);

    if (!s_mounted) return ESP_ERR_INVALID_STATE;

    struct stat st;
    const bool fresh = (stat(BG_LOG_FILE, &st) != 0 || st.st_size == 0);
    FILE *f = fopen(BG_LOG_FILE, "a");
    if (!f) return ESP_FAIL;
    if (fresh) {
        fprintf(f, "wake_no,uptime_ms,reset,n_new,n_window,soil_mv,"
                   "soil_vwc,soil_safe,batt_mv,cycle_min,action,reason\n");
    }
    fprintf(f, "%lu,%lu,%s,%u,%u,%d,%.1f,%u,%u,%lu,%s,%s\n",
            (unsigned long)r->wake_no, (unsigned long)r->uptime_ms,
            reset_reason_str(esp_reset_reason()), r->n_new, r->n_window,
            r->soil_mv, r->soil_vwc, r->soil_safe, r->batt_mv,
            (unsigned long)r->cycle_min, r->action, r->reason);
    fclose(f); /* fclose flushes to flash — record survives the sleep/crash */
    return ESP_OK;
}

void event_log_close(void)
{
    if (s_mounted) esp_vfs_spiffs_unregister("storage");
    esp_task_wdt_delete(NULL);
}
