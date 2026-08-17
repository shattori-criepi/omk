#include "wifi_station.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_wifi.h"

static const char *TAG = "omk-wifi";

static void station_event_handler(void *arg, esp_event_base_t event_base,
                                  int32_t event_id, void *event_data) {
    (void)arg;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = event_data;
        ESP_LOGW(TAG, "Wi-Fi disconnected (reason %d); reconnecting",
                 event != NULL ? event->reason : -1);
        esp_err_t err = esp_wifi_connect();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi reconnect request failed: %s", esp_err_to_name(err));
        }
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = event_data;
        if (event != NULL) {
            ESP_LOGI(TAG, "Wi-Fi connected; IP=" IPSTR,
                     IP2STR(&event->ip_info.ip));
        } else {
            ESP_LOGI(TAG, "Wi-Fi connected");
        }
    }
}

esp_err_t wifi_station_prepare(bool *has_saved_credentials) {
    if (has_saved_credentials == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *has_saved_credentials = false;
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

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init_config);
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

    wifi_config_t config = {0};
    err = esp_wifi_get_config(WIFI_IF_STA, &config);
    if (err != ESP_OK) {
        return err;
    }
    if (config.sta.ssid[0] == '\0') {
        ESP_LOGI(TAG, "Wi-Fi not provisioned");
        return ESP_OK;
    }
    *has_saved_credentials = true;

    return ESP_OK;
}

esp_err_t wifi_station_start_prepared(void) {
    esp_err_t err = esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
                                               station_event_handler, NULL);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                     station_event_handler, NULL);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_start();
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_connect();
    if (err != ESP_OK) {
        return err;
    }

    ESP_LOGI(TAG, "Starting Wi-Fi STA connection using saved credentials");
    return ESP_OK;
}
esp_err_t wifi_station_save_credentials(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length) {
    if (ssid == NULL || password == NULL || ssid_length == 0 ||
        ssid_length > sizeof(((wifi_config_t *)0)->sta.ssid) ||
        password_length < 8 || password_length > sizeof(((wifi_config_t *)0)->sta.password)) {
        return ESP_ERR_INVALID_ARG;
    }

    wifi_config_t expected_config = {0};
    memcpy(expected_config.sta.ssid, ssid, ssid_length);
    memcpy(expected_config.sta.password, password, password_length);

    esp_err_t err = esp_wifi_set_storage(WIFI_STORAGE_FLASH);
    if (err == ESP_OK) {
        err = esp_wifi_set_config(WIFI_IF_STA, &expected_config);
    }
    if (err == ESP_OK) {
        wifi_config_t verified_config = {0};
        err = esp_wifi_get_config(WIFI_IF_STA, &verified_config);
        if (err == ESP_OK &&
            (memcmp(verified_config.sta.ssid, expected_config.sta.ssid,
                    sizeof(expected_config.sta.ssid)) != 0 ||
             memcmp(verified_config.sta.password, expected_config.sta.password,
                    sizeof(expected_config.sta.password)) != 0)) {
            err = ESP_FAIL;
        }
    }
    return err;
}

esp_err_t wifi_station_set_saved_credentials_for_development(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length) {
    if (ssid == NULL || password == NULL || ssid_length == 0 ||
        ssid_length > sizeof(((wifi_config_t *)0)->sta.ssid) ||
        password_length < 8 || password_length > sizeof(((wifi_config_t *)0)->sta.password)) {
        return ESP_ERR_INVALID_ARG;
    }

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err = esp_wifi_init(&init_config);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_set_storage(WIFI_STORAGE_FLASH);
    if (err == ESP_OK) {
        err = esp_wifi_set_mode(WIFI_MODE_STA);
    }
    if (err == ESP_OK) {
        err = wifi_station_save_credentials(ssid, ssid_length,
                                            password, password_length);
    }

    esp_err_t deinit_err = esp_wifi_deinit();
    return err == ESP_OK ? deinit_err : err;
}
