#include <string.h>
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_mac.h"
#include "esp_bt.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "discovery_ble.h"
#include "node_state.h"
#include "mqtt_registration.h"
#include "node_registration.h"
#include "node_protocol.h"
#include "provisioning.h"
#include "wifi_station.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_ble.h"

#ifdef OMK_DEVELOPMENT_SET_WIFI_CREDENTIALS
#include "development_wifi_config.h"
#endif

#define FACTORY_MAGIC "OMKP"
#define POP_BYTES 32
static const char *TAG = "omk-node";

typedef struct __attribute__((packed)) { char magic[4]; unsigned char pop[POP_BYTES]; } factory_secret_t;

typedef enum {
    BOOT_FLOW_DISCOVERY,
    BOOT_FLOW_PROVISIONING_IDLE,
    BOOT_FLOW_SAFE_IDLE,
} boot_flow_t;

static uint64_t node_id(void) {
    uint8_t mac[6]; ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
    uint64_t hash = 14695981039346656037ULL;
    for (size_t i = 0; i < sizeof(mac); ++i) { hash ^= mac[i]; hash *= 1099511628211ULL; }
    return hash & 0x0000ffffffffffffULL;
}


/* Idempotent: factory data remains authoritative until an NVS read-back matches.
 * Thus a power cut before erase simply retries on the next boot. */
static void import_factory_pop(void) {
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "factory_secret");
    factory_secret_t source, verified;
    if (!part || esp_partition_read(part, 0, &source, sizeof(source)) != ESP_OK || memcmp(source.magic, FACTORY_MAGIC, 4)) return;
    nvs_handle_t nvs;
    ESP_ERROR_CHECK(nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs));
    size_t length = sizeof(verified.pop);
    esp_err_t existing = nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, verified.pop, &length);
    if (existing == ESP_OK && length == sizeof(verified.pop) && !memcmp(verified.pop, source.pop, POP_BYTES)) {
        ESP_ERROR_CHECK(esp_partition_erase_range(part, 0, part->size)); nvs_close(nvs); return;
    }
    ESP_ERROR_CHECK(nvs_set_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, source.pop, POP_BYTES));
    ESP_ERROR_CHECK(nvs_commit(nvs));
    length = sizeof(verified.pop);
    ESP_ERROR_CHECK(nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, verified.pop, &length));
    if (length == sizeof(verified.pop) && !memcmp(verified.pop, source.pop, POP_BYTES)) ESP_ERROR_CHECK(esp_partition_erase_range(part, 0, part->size));
    nvs_close(nvs);
}

static bool clear_next_boot_mode(nvs_handle_t nvs) {
    esp_err_t err = nvs_erase_key(nvs, NODE_NVS_NEXT_BOOT_MODE_KEY);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to clear provisioning boot request: %s",
                 esp_err_to_name(err));
        return false;
    }
    return true;
}

static boot_flow_t select_boot_flow(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot open boot mode storage: %s", esp_err_to_name(err));
        return BOOT_FLOW_SAFE_IDLE;
    }

    uint8_t mode;
    err = nvs_get_u8(nvs, NODE_NVS_NEXT_BOOT_MODE_KEY, &mode);
    if (err == ESP_ERR_NVS_NOT_FOUND || (err == ESP_OK && mode == NODE_BOOT_MODE_DEFAULT)) {
        ESP_LOGI(TAG, "Boot flow selected: discovery");
        nvs_close(nvs);
        return BOOT_FLOW_DISCOVERY;
    }

    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Invalid provisioning boot request; clearing it");
    } else if (mode == NODE_BOOT_MODE_PROVISIONING) {
        ESP_LOGI(TAG, "Provisioning boot request detected");
        /* Clear before provisioning so a crash, watchdog reset, or power loss
         * cannot cause an endless provisioning-boot loop. */
        bool cleared = clear_next_boot_mode(nvs);
        nvs_close(nvs);
        return cleared ? BOOT_FLOW_PROVISIONING_IDLE : BOOT_FLOW_SAFE_IDLE;
    } else {
        ESP_LOGW(TAG, "Unknown boot mode %u; clearing it", (unsigned)mode);
    }

    bool cleared = clear_next_boot_mode(nvs);
    nvs_close(nvs);
    return cleared ? BOOT_FLOW_DISCOVERY : BOOT_FLOW_SAFE_IDLE;
}

void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
#ifdef OMK_DEVELOPMENT_CLEAR_REGISTRATION
    /* This image is built only by reset-omk-node-registration.sh.  Keep the
     * operation before all normal boot work so it cannot alter Wi-Fi, PoP, or
     * factory-secret state.  The script immediately restores production FW. */
    esp_err_t reset_err = node_registration_clear_for_development();
    if (reset_err == ESP_OK) {
        ESP_LOGI(TAG, "Development registration reset verified");
    } else {
        ESP_LOGE(TAG, "Development registration reset failed: %s", esp_err_to_name(reset_err));
    }
    return;
#endif
#ifdef OMK_DEVELOPMENT_SET_WIFI_CREDENTIALS
    /* This image is built only by set-omk-node-wifi.sh. It verifies the
     * node-specific PoP before and after writing only ESP-IDF's persisted STA
     * configuration. The script immediately restores production firmware. */
    esp_err_t set_err = node_state_verify_provisioning_pop();
    if (set_err == ESP_OK) {
        set_err = wifi_station_set_saved_credentials_for_development(
            OMK_DEVELOPMENT_WIFI_SSID, OMK_DEVELOPMENT_WIFI_SSID_LENGTH,
            OMK_DEVELOPMENT_WIFI_PSK, OMK_DEVELOPMENT_WIFI_PSK_LENGTH);
    }
    if (set_err == ESP_OK) {
        set_err = node_state_verify_provisioning_pop();
    }
    if (set_err == ESP_OK) {
        ESP_LOGI(TAG, "Development Wi-Fi credential write verified");
    } else {
        ESP_LOGE(TAG, "Development Wi-Fi credential write failed: %s",
                 esp_err_to_name(set_err));
    }
    return;
#endif
    import_factory_pop();
    ESP_LOGI(TAG, "Factory provisioning state initialized");

    boot_flow_t boot_flow = select_boot_flow();
    if (boot_flow == BOOT_FLOW_PROVISIONING_IDLE) {
        ESP_LOGI(TAG, "Provisioning boot requested");
        /* Legacy Control-GATT boot flag path. It owns the network stack in
         * this boot because Discovery has not initialized Wi-Fi. */
        esp_err_t err = provisioning_start(node_id(), false);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Provisioning boot failed; entering safe idle: %s",
                     esp_err_to_name(err));
        }
        return;
    }
    if (boot_flow == BOOT_FLOW_SAFE_IDLE) {
        ESP_LOGE(TAG, "Entering safe idle; BLE will not be started");
        return;
    }

    ESP_LOGI(TAG, "Starting normal boot");
    bool has_wifi_credentials = false;
    esp_err_t wifi_err = wifi_station_prepare(&has_wifi_credentials);
    if (wifi_err != ESP_OK) {
        ESP_LOGW(TAG, "Wi-Fi credential check failed; discovery continues: %s",
                 esp_err_to_name(wifi_err));
    } else if (!has_wifi_credentials) {
        /* No saved STA configuration means this boot belongs exclusively to
         * Espressif provisioning BLE. wifi_station_prepare() has already
         * initialized the shared network stack, so do not initialize it
         * again in provisioning_start(). */
        ESP_LOGI(TAG, "Wi-Fi not provisioned; starting provisioning service");
        esp_err_t provisioning_err = provisioning_start(node_id(), true);
        if (provisioning_err != ESP_OK) {
            ESP_LOGE(TAG, "Direct provisioning boot failed: %s",
                     esp_err_to_name(provisioning_err));
        }
        return;
    } else {
        wifi_err = wifi_station_start_prepared();
        if (wifi_err != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi station startup failed; discovery continues: %s",
                     esp_err_to_name(wifi_err));
        } else {
            esp_err_t mqtt_err = mqtt_registration_start(node_id());
            if (mqtt_err != ESP_OK) {
                ESP_LOGW(TAG, "MQTT registration startup failed; discovery continues: %s",
                         esp_err_to_name(mqtt_err));
            }
        }
    }
    // Safe once per boot for BLE-only operation; never repeat this in a
    // discovery lifecycle because the released Classic BT memory is permanent.
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    ESP_ERROR_CHECK(node_state_start());
    uint8_t provisioning_state = has_wifi_credentials
        ? OMK_NODE_PROVISIONING_STATE_PROVISIONED
        : OMK_NODE_PROVISIONING_STATE_UNREGISTERED;
    esp_err_t registration_err = node_registration_get_provisioning_state(
        has_wifi_credentials, &provisioning_state);
    if (registration_err != ESP_OK) {
        ESP_LOGW(TAG, "Could not read registration state; using Wi-Fi state: %s",
                 esp_err_to_name(registration_err));
    }
    ESP_ERROR_CHECK(discovery_ble_start(node_id(), provisioning_state));
    ESP_LOGI(TAG, "OMK Node discovery advertising started");
}
