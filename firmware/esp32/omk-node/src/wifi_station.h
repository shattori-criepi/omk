#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* Initializes shared netif/event loop/Wi-Fi state and checks the persisted
 * STA configuration. The caller then starts either STA or provisioning. */
esp_err_t wifi_station_prepare(bool *has_saved_credentials);
esp_err_t wifi_station_start_prepared(void);

/* Persists an STA configuration through ESP-IDF's Wi-Fi storage and verifies
 * the read-back. The caller owns Wi-Fi driver initialization and lifecycle. */
esp_err_t wifi_station_save_credentials(const uint8_t *ssid, size_t ssid_length,
                                        const uint8_t *password, size_t password_length);

/* Development setter-image helper. It persists only the supplied STA
 * configuration using the ESP-IDF Wi-Fi storage, reads it back, and
 * deinitializes the driver. It does not access namespace omk. */
esp_err_t wifi_station_set_saved_credentials_for_development(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length);
