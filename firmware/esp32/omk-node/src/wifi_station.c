#include "wifi_station.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_wifi.h"
#include "gateway_credentials.h"
#include "nvs.h"

static const char *TAG = "omk-wifi";

esp_err_t wifi_station_init_network_core(void) {
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK) {
        return err;
    }

    err = esp_event_loop_create_default();
    if (err != ESP_OK) {
        return err;
    }
    return ESP_OK;
}

esp_err_t wifi_station_prepare(bool *has_saved_credentials) {
    if (has_saved_credentials == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *has_saved_credentials = false;

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err = esp_wifi_init(&init_config);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_set_storage(WIFI_STORAGE_FLASH);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err != ESP_OK) {
        return err;
    }

    err = gateway_credentials_clear_legacy_for_setup();
    if (err != ESP_OK) return err;

    omk_gateway_credentials_t credentials;
    err = gateway_credentials_load(&credentials);
    if (err == ESP_OK) {
        *has_saved_credentials = true;
        return ESP_OK;
    }
    if (err != ESP_ERR_NVS_NOT_FOUND) return err;

    wifi_config_t legacy = {0};
    err = esp_wifi_get_config(WIFI_IF_STA, &legacy);
    if (err != ESP_OK) return err;
    bool migrated = false;
    err = gateway_credentials_migrate_legacy(&legacy, &migrated);
    if (err == ESP_OK && migrated) {
        ESP_LOGI(TAG, "Migrated legacy Gateway credential to OMK credential store");
        *has_saved_credentials = true;
        return ESP_OK;
    }
    if (err == ESP_ERR_NOT_SUPPORTED) {
        if (legacy.sta.ssid[0] != '\0') {
            ESP_LOGW(TAG, "Legacy STA state is not a safe Gateway credential; USB re-provisioning required");
        } else {
            ESP_LOGI(TAG, "Wi-Fi not provisioned");
        }
        return ESP_OK;
    }
    return err;
}

esp_err_t wifi_station_save_credentials(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length) {
    return gateway_credentials_save(ssid, ssid_length, password, password_length);
}

esp_err_t wifi_station_clear_saved_credentials(void) {
    return gateway_credentials_clear();
}

esp_err_t wifi_station_has_saved_credentials(bool *configured) {
    if (configured == NULL) return ESP_ERR_INVALID_ARG;
    omk_gateway_credentials_t credentials;
    esp_err_t err = gateway_credentials_load(&credentials);
    if (err == ESP_OK) {
        *configured = true;
        return ESP_OK;
    }
    *configured = false;
    return err == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : err;
}

esp_err_t wifi_station_set_saved_credentials_for_development(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length) {
    if (ssid == NULL || password == NULL || ssid_length == 0 ||
        ssid_length > sizeof(((wifi_config_t *)0)->sta.ssid) ||
        password_length < 8 || password_length > sizeof(((wifi_config_t *)0)->sta.password)) {
        return ESP_ERR_INVALID_ARG;
    }

    return wifi_station_save_credentials(ssid, ssid_length, password, password_length);
}
