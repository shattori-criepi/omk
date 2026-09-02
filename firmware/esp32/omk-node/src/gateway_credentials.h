#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_wifi_types.h"

#define OMK_GATEWAY_SSID_MAX_LENGTH 32
#define OMK_GATEWAY_PSK_MAX_LENGTH 64

typedef struct {
    uint8_t ssid[OMK_GATEWAY_SSID_MAX_LENGTH];
    size_t ssid_length;
    uint8_t psk[OMK_GATEWAY_PSK_MAX_LENGTH];
    size_t psk_length;
} omk_gateway_credentials_t;

esp_err_t gateway_credentials_save(const uint8_t *ssid, size_t ssid_length,
                                   const uint8_t *psk, size_t psk_length);
/* Erases only omk_net/gw_cred and verifies the key is absent after commit. */
esp_err_t gateway_credentials_clear(void);
esp_err_t gateway_credentials_load(omk_gateway_credentials_t *credentials);
esp_err_t gateway_credentials_migrate_legacy(const wifi_config_t *legacy,
                                              bool *migrated);
