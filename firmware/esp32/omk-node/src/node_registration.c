#include "node_registration.h"

#include <string.h>

#include "nvs.h"
#include "node_protocol.h"
#include "node_state.h"

#define OMK_NVS_REGISTERED_KEY "registered"
#define OMK_NVS_LOGICAL_ID_KEY "logical_id"

static esp_err_t read_registered(bool *registered) {
    nvs_handle_t nvs;
    uint8_t value = 0;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_get_u8(nvs, OMK_NVS_REGISTERED_KEY, &value);
    nvs_close(nvs);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        *registered = false;
        return ESP_OK;
    }
    if (err != ESP_OK) {
        return err;
    }
    *registered = value == 1;
    return ESP_OK;
}

esp_err_t node_registration_get_provisioning_state(bool has_wifi_credentials,
                                                   uint8_t *state) {
    if (state == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    bool registered = false;
    esp_err_t err = read_registered(&registered);
    if (err != ESP_OK) {
        return err;
    }
    if (registered) {
        *state = OMK_NODE_PROVISIONING_STATE_REGISTERED;
    } else if (has_wifi_credentials) {
        *state = OMK_NODE_PROVISIONING_STATE_PROVISIONED;
    } else {
        *state = OMK_NODE_PROVISIONING_STATE_UNREGISTERED;
    }
    return ESP_OK;
}

esp_err_t node_registration_save(const char *logical_id) {
    if (logical_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_str(nvs, OMK_NVS_LOGICAL_ID_KEY, logical_id);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, OMK_NVS_REGISTERED_KEY, 1);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (err != ESP_OK) {
        nvs_close(nvs);
        return err;
    }

    char readback_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    size_t readback_length = sizeof(readback_id);
    uint8_t readback_registered = 0;
    err = nvs_get_str(nvs, OMK_NVS_LOGICAL_ID_KEY, readback_id, &readback_length);
    if (err == ESP_OK) {
        err = nvs_get_u8(nvs, OMK_NVS_REGISTERED_KEY, &readback_registered);
    }
    nvs_close(nvs);
    if (err != ESP_OK || readback_registered != 1 || strcmp(readback_id, logical_id) != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t node_registration_clear_for_development(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_erase_key(nvs, OMK_NVS_REGISTERED_KEY);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;
    }
    if (err == ESP_OK) {
        err = nvs_erase_key(nvs, OMK_NVS_LOGICAL_ID_KEY);
        if (err == ESP_ERR_NVS_NOT_FOUND) {
            err = ESP_OK;
        }
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (err != ESP_OK) {
        nvs_close(nvs);
        return err;
    }

    uint8_t registered = 0;
    err = nvs_get_u8(nvs, OMK_NVS_REGISTERED_KEY, &registered);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        size_t logical_id_length = 0;
        err = nvs_get_str(nvs, OMK_NVS_LOGICAL_ID_KEY, NULL, &logical_id_length);
    }
    nvs_close(nvs);
    return err == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : (err == ESP_OK ? ESP_FAIL : err);
}
