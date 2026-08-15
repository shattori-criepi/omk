#include "provisioning.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_ble.h"
#include "nvs.h"
#include "node_state.h"

#define PROVISIONING_POP_HEX_LENGTH (NODE_PROVISIONING_POP_LENGTH * 2)

static const char *TAG = "omk-provisioning";

/* Canonical UUID c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27, LSB -> MSB. */
static uint8_t provisioning_service_uuid[16] = {
    0x27, 0x1d, 0xbc, 0xa3, 0x89, 0x4f, 0x6d, 0x9e,
    0x81, 0x4f, 0xfd, 0x75, 0x31, 0x8e, 0xf0, 0xc2,
};

/* The manager API accepts a NUL-terminated Security 1 PoP string. Keep this
 * buffer alive for the whole provisioning service lifetime. */
static char provisioning_pop_hex[PROVISIONING_POP_HEX_LENGTH + 1];

static esp_err_t load_provisioning_pop_hex(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Provisioning PoP is unavailable");
        return err;
    }

    size_t length = 0;
    err = nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, NULL, &length);
    if (err != ESP_OK || length != NODE_PROVISIONING_POP_LENGTH) {
        nvs_close(nvs);
        ESP_LOGE(TAG, "Provisioning PoP is missing or has invalid length");
        return err == ESP_OK ? ESP_FAIL : err;
    }

    uint8_t raw_pop[NODE_PROVISIONING_POP_LENGTH];
    length = sizeof(raw_pop);
    err = nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, raw_pop, &length);
    nvs_close(nvs);
    if (err != ESP_OK || length != sizeof(raw_pop)) {
        ESP_LOGE(TAG, "Failed to read provisioning PoP");
        return err == ESP_OK ? ESP_FAIL : err;
    }

    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof(raw_pop); ++i) {
        provisioning_pop_hex[i * 2] = hex[raw_pop[i] >> 4];
        provisioning_pop_hex[i * 2 + 1] = hex[raw_pop[i] & 0x0f];
    }
    provisioning_pop_hex[PROVISIONING_POP_HEX_LENGTH] = '\0';
    memset(raw_pop, 0, sizeof(raw_pop));
    return ESP_OK;
}

static void provisioning_event_handler(void *arg, esp_event_base_t event_base,
                                       int32_t event_id, void *event_data) {
    (void)arg;
    if (event_base != NETWORK_PROV_EVENT) {
        return;
    }

    switch (event_id) {
    case NETWORK_PROV_START:
        ESP_LOGI(TAG, "Provisioning service started");
        break;
    case NETWORK_PROV_WIFI_CRED_RECV:
        ESP_LOGI(TAG, "Wi-Fi credential received");
        break;
    case NETWORK_PROV_WIFI_CRED_FAIL:
        if (event_data != NULL) {
            ESP_LOGW(TAG, "Wi-Fi credential connection failed (reason %d)",
                     *(network_prov_wifi_sta_fail_reason_t *)event_data);
        } else {
            ESP_LOGW(TAG, "Wi-Fi credential connection failed");
        }
        break;
    case NETWORK_PROV_WIFI_CRED_SUCCESS:
        ESP_LOGI(TAG, "Wi-Fi credential connection succeeded");
        break;
    case NETWORK_PROV_END: {
        ESP_LOGI(TAG, "Provisioning service ended");
        esp_err_t err = network_prov_mgr_deinit();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to de-initialize provisioning manager: %s",
                     esp_err_to_name(err));
        }
        break;
    }
    default:
        break;
    }
}

static esp_err_t init_provisioning_wifi(void) {
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK) {
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK) {
        return err;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        return ESP_ERR_NO_MEM;
    }
    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    return esp_wifi_init(&wifi_config);
}

esp_err_t provisioning_start(uint64_t node_id) {
    esp_err_t err = load_provisioning_pop_hex();
    if (err != ESP_OK) {
        return err;
    }

    err = init_provisioning_wifi();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize provisioning network stack: %s",
                 esp_err_to_name(err));
        return err;
    }

    err = esp_event_handler_register(NETWORK_PROV_EVENT, ESP_EVENT_ANY_ID,
                                     provisioning_event_handler, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to register provisioning event handler: %s",
                 esp_err_to_name(err));
        return err;
    }

    network_prov_mgr_config_t config = {
        .scheme = network_prov_scheme_ble,
        .scheme_event_handler = NETWORK_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM,
        .app_event_handler = NETWORK_PROV_EVENT_HANDLER_NONE,
    };
    err = network_prov_mgr_init(config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize provisioning manager: %s",
                 esp_err_to_name(err));
        return err;
    }

    err = network_prov_scheme_ble_set_service_uuid(provisioning_service_uuid);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set provisioning BLE service UUID: %s",
                 esp_err_to_name(err));
        network_prov_mgr_deinit();
        return err;
    }

    char service_name[30];
    int name_length = snprintf(service_name, sizeof(service_name), "OMK_%012" PRIx64,
                               node_id & UINT64_C(0x0000ffffffffffff));
    if (name_length < 0 || name_length >= (int)sizeof(service_name)) {
        network_prov_mgr_deinit();
        return ESP_FAIL;
    }

    network_prov_security1_params_t *sec_params = provisioning_pop_hex;
    err = network_prov_mgr_start_provisioning(
        NETWORK_PROV_SECURITY_1, (const void *)sec_params, service_name, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start Security 1 provisioning: %s",
                 esp_err_to_name(err));
        network_prov_mgr_deinit();
        return err;
    }
    return ESP_OK;
}
