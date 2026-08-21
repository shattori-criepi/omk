#include <string.h>
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_bt.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "discovery_ble.h"
#include "node_state.h"
#include "mqtt_registration.h"
#include "mesh_network.h"
#include "mesh_netif.h"
#include "node_registration.h"
#include "node_identity.h"
#include "node_protocol.h"
#include "sensor_manager.h"
#include "usb_provisioning.h"
#include "wifi_station.h"

#ifdef OMK_DEVELOPMENT_SET_WIFI_CREDENTIALS
#include "development_wifi_config.h"
#endif

#define FACTORY_MAGIC "OMKP"
#define POP_BYTES 32
static const char *TAG = "omk-node";

typedef struct __attribute__((packed)) { char magic[4]; unsigned char pop[POP_BYTES]; } factory_secret_t;

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

    ESP_LOGI(TAG, "Normal boot begin");
    uint64_t node_id;
    ESP_ERROR_CHECK(node_identity_get_id(&node_id));
    bool has_wifi_credentials = false;
    esp_err_t wifi_err = wifi_station_init_network_core();
    if (wifi_err == ESP_OK) {
        /* Match Espressif's Mesh examples: create and attach the default STA
         * netif before esp_wifi_init(), so its event handlers own the first
         * root association. */
        wifi_err = mesh_netifs_init();
    }
    if (wifi_err == ESP_OK) wifi_err = wifi_station_prepare(&has_wifi_credentials);
    if (wifi_err != ESP_OK) {
        ESP_LOGW(TAG, "Wi-Fi credential check failed; discovery continues: %s",
                 esp_err_to_name(wifi_err));
    } else if (has_wifi_credentials) {
        ESP_LOGI(TAG, "Starting Mesh network");
        wifi_err = mesh_network_start_prepared();
        if (wifi_err != ESP_OK) {
            ESP_LOGW(TAG, "Mesh network start failed: %s; discovery continues",
                     esp_err_to_name(wifi_err));
        } else {
            ESP_LOGI(TAG, "Mesh network started; starting MQTT registration");
            esp_err_t mqtt_err = mqtt_registration_start();
            if (mqtt_err != ESP_OK) {
                ESP_LOGW(TAG, "MQTT registration start failed: %s; discovery continues",
                         esp_err_to_name(mqtt_err));
            } else {
                ESP_LOGI(TAG, "MQTT registration initialized");
            }
        }
    }
    /* USB Serial/JTAG is available during the normal application lifecycle.
     * It replaces the temporary setup AP without requiring a button, a reset,
     * or a gateway wlan0 mode transition. */
    esp_err_t usb_err = usb_provisioning_start(node_id);
    if (usb_err != ESP_OK) {
        ESP_LOGE(TAG, "USB provisioning transport failed: %s", esp_err_to_name(usb_err));
    }
    esp_err_t sensor_err = sensor_manager_start();
    if (sensor_err != ESP_OK) {
        ESP_LOGW(TAG, "Sensor manager start failed: %s; continuing",
                 esp_err_to_name(sensor_err));
    } else {
        ESP_LOGI(TAG, "Sensor manager started");
    }
    // Safe once per boot for BLE-only operation; never repeat this in a
    // discovery lifecycle because the released Classic BT memory is permanent.
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    uint8_t provisioning_state = has_wifi_credentials
        ? OMK_NODE_PROVISIONING_STATE_PROVISIONED
        : OMK_NODE_PROVISIONING_STATE_UNREGISTERED;
    esp_err_t registration_err = node_registration_get_provisioning_state(
        has_wifi_credentials, &provisioning_state);
    if (registration_err != ESP_OK) {
        ESP_LOGW(TAG, "Could not read registration state; using Wi-Fi state: %s",
                 esp_err_to_name(registration_err));
    }
    ESP_ERROR_CHECK(discovery_ble_start(node_id, provisioning_state));
    ESP_LOGI(TAG, "OMK Node discovery advertising started");
}
