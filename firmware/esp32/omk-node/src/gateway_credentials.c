#include "gateway_credentials.h"

#include <string.h>

#include "nvs.h"
#include "esp_wifi.h"

#define OMK_GATEWAY_CREDENTIALS_NAMESPACE "omk_net"
#define OMK_GATEWAY_CREDENTIALS_KEY "gw_cred"
#define OMK_GATEWAY_CREDENTIALS_VERSION 1

typedef struct {
    uint8_t version;
    uint8_t ssid_length;
    uint8_t psk_length;
    uint8_t reserved;
    uint8_t ssid[OMK_GATEWAY_SSID_MAX_LENGTH];
    uint8_t psk[OMK_GATEWAY_PSK_MAX_LENGTH];
} gateway_credentials_record_t;

static bool lengths_are_valid(size_t ssid_length, size_t psk_length) {
    return ssid_length > 0 && ssid_length <= OMK_GATEWAY_SSID_MAX_LENGTH &&
           psk_length >= 8 && psk_length <= OMK_GATEWAY_PSK_MAX_LENGTH;
}

static bool is_mesh_default_ap_ssid(const uint8_t *ssid, size_t length) {
    static const uint8_t prefix[] = {'E', 'S', 'P', 'M', '_'};
    return length >= sizeof(prefix) && memcmp(ssid, prefix, sizeof(prefix)) == 0;
}

esp_err_t gateway_credentials_save(const uint8_t *ssid, size_t ssid_length,
                                   const uint8_t *psk, size_t psk_length) {
    if (ssid == NULL || psk == NULL || !lengths_are_valid(ssid_length, psk_length)) {
        return ESP_ERR_INVALID_ARG;
    }
    gateway_credentials_record_t expected = {
        .version = OMK_GATEWAY_CREDENTIALS_VERSION,
        .ssid_length = (uint8_t)ssid_length,
        .psk_length = (uint8_t)psk_length,
    };
    memcpy(expected.ssid, ssid, ssid_length);
    memcpy(expected.psk, psk, psk_length);
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(OMK_GATEWAY_CREDENTIALS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) err = nvs_set_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY, &expected, sizeof(expected));
    if (err == ESP_OK) err = nvs_commit(nvs);
    gateway_credentials_record_t verified = {0};
    size_t length = sizeof(verified);
    if (err == ESP_OK) err = nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY, &verified, &length);
    if (err == ESP_OK && (length != sizeof(verified) || memcmp(&expected, &verified, sizeof(expected)) != 0)) {
        err = ESP_FAIL;
    }
    if (nvs != 0) nvs_close(nvs);
    memset(&expected, 0, sizeof(expected));
    memset(&verified, 0, sizeof(verified));
    return err;
}

esp_err_t gateway_credentials_load(omk_gateway_credentials_t *credentials) {
    if (credentials == NULL) return ESP_ERR_INVALID_ARG;
    memset(credentials, 0, sizeof(*credentials));
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(OMK_GATEWAY_CREDENTIALS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) return err;
    gateway_credentials_record_t record = {0};
    size_t length = sizeof(record);
    err = nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY, &record, &length);
    nvs_close(nvs);
    if (err != ESP_OK) return err;
    if (length != sizeof(record) || record.version != OMK_GATEWAY_CREDENTIALS_VERSION ||
        !lengths_are_valid(record.ssid_length, record.psk_length)) {
        memset(&record, 0, sizeof(record));
        return ESP_ERR_INVALID_STATE;
    }
    memcpy(credentials->ssid, record.ssid, record.ssid_length);
    credentials->ssid_length = record.ssid_length;
    memcpy(credentials->psk, record.psk, record.psk_length);
    credentials->psk_length = record.psk_length;
    memset(&record, 0, sizeof(record));
    return ESP_OK;
}

esp_err_t gateway_credentials_clear(void) {
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(OMK_GATEWAY_CREDENTIALS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) err = nvs_erase_key(nvs, OMK_GATEWAY_CREDENTIALS_KEY);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    if (err == ESP_OK) err = nvs_commit(nvs);
    if (err == ESP_OK) {
        gateway_credentials_record_t record = {0};
        size_t length = sizeof(record);
        err = nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY, &record, &length);
        memset(&record, 0, sizeof(record));
        if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
        else if (err == ESP_OK) err = ESP_FAIL;
    }
    if (nvs != 0) nvs_close(nvs);
    return err;
}

/* The marker survives a power cut between factory import and Wi-Fi init.
 * Clear ESP-IDF's STA configuration before any legacy migration or Mesh start.
 * Do not erase the Wi-Fi namespace (calibration and other state may live there). */
esp_err_t gateway_credentials_reset_for_setup(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(OMK_GATEWAY_CREDENTIALS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) return err;
    err = nvs_set_u8(nvs, "reset_sta", 1);
    if (err == ESP_OK) err = nvs_commit(nvs);
    uint8_t verified = 0;
    if (err == ESP_OK) err = nvs_get_u8(nvs, "reset_sta", &verified);
    nvs_close(nvs);
    if (err != ESP_OK) return err;
    if (verified != 1) return ESP_FAIL;
    return gateway_credentials_clear();
}

esp_err_t gateway_credentials_clear_legacy_for_setup(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(OMK_GATEWAY_CREDENTIALS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) return err;
    uint8_t pending = 0;
    err = nvs_get_u8(nvs, "reset_sta", &pending);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    if (err == ESP_OK && pending == 1) {
        wifi_config_t empty = {0}, verified = {0};
        err = esp_wifi_set_config(WIFI_IF_STA, &empty);
        if (err == ESP_OK) err = esp_wifi_get_config(WIFI_IF_STA, &verified);
        if (err == ESP_OK && (memcmp(empty.sta.ssid, verified.sta.ssid, sizeof(empty.sta.ssid)) != 0 ||
                             memcmp(empty.sta.password, verified.sta.password, sizeof(empty.sta.password)) != 0 ||
                             verified.sta.bssid_set)) err = ESP_FAIL;
        if (err == ESP_OK) err = nvs_erase_key(nvs, "reset_sta");
        if (err == ESP_OK) err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

esp_err_t gateway_credentials_migrate_legacy(const wifi_config_t *legacy, bool *migrated) {
    if (legacy == NULL || migrated == NULL) return ESP_ERR_INVALID_ARG;
    *migrated = false;
    size_t ssid_length = strnlen((const char *)legacy->sta.ssid, sizeof(legacy->sta.ssid));
    size_t psk_length = strnlen((const char *)legacy->sta.password, sizeof(legacy->sta.password));
    /* Old USB provisioning never set BSSID. A BSSID-pinned STA is Mesh's
     * parent association state, not evidence of a Gateway router. */
    if (!lengths_are_valid(ssid_length, psk_length) || legacy->sta.bssid_set ||
        is_mesh_default_ap_ssid(legacy->sta.ssid, ssid_length)) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    esp_err_t err = gateway_credentials_save(legacy->sta.ssid, ssid_length,
                                             legacy->sta.password, psk_length);
    if (err == ESP_OK) *migrated = true;
    return err;
}
