#include "boot_diagnostics.h"

#include <inttypes.h>
#include <limits.h>

#include "esp_log.h"
#include "esp_system.h"
#include "nvs.h"

#define OMK_BOOT_DIAGNOSTICS_NAMESPACE "omk_diag"
#define OMK_BOOT_COUNT_KEY "boot_count"

static const char *TAG = "omk-boot-diag";
static esp_reset_reason_t reset_reason = ESP_RST_UNKNOWN;
static uint32_t boot_count;

static const char *reset_reason_name(esp_reset_reason_t reason) {
    switch (reason) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_INT_WDT: return "interrupt_watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    default: return "unknown";
    }
}

void boot_diagnostics_init(void) {
    reset_reason = esp_reset_reason();
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(OMK_BOOT_DIAGNOSTICS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Boot counter NVS open failed: %s", esp_err_to_name(err));
        goto log_result;
    }
    uint32_t previous_count = 0;
    err = nvs_get_u32(nvs, OMK_BOOT_COUNT_KEY, &previous_count);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW(TAG, "Boot counter NVS read failed: %s", esp_err_to_name(err));
        nvs_close(nvs);
        goto log_result;
    }
    boot_count = previous_count < UINT32_MAX ? previous_count + 1 : UINT32_MAX;
    err = nvs_set_u32(nvs, OMK_BOOT_COUNT_KEY, boot_count);
    if (err == ESP_OK) err = nvs_commit(nvs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Boot counter NVS write failed: %s", esp_err_to_name(err));
    }
    nvs_close(nvs);
log_result:
    ESP_LOGI(TAG, "Boot diagnostics: reset_reason=%s code=%d boot_count=%" PRIu32,
             reset_reason_name(reset_reason), (int)reset_reason, boot_count);
}

const char *boot_diagnostics_reset_reason(void) { return reset_reason_name(reset_reason); }
uint32_t boot_diagnostics_reset_reason_code(void) { return (uint32_t)reset_reason; }
uint32_t boot_diagnostics_boot_count(void) { return boot_count; }
