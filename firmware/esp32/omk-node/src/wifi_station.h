#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* Initializes the shared netif and default event loop before Mesh creates the
 * default STA netif. */
esp_err_t wifi_station_init_network_core(void);

/* Initializes the Wi-Fi driver, then checks the persisted STA configuration.
 * Mesh networking owns the STA netif lifecycle. */
esp_err_t wifi_station_prepare(bool *has_saved_credentials);

/* Persists Gateway credential material independently of ESP-WIFI-MESH's
 * runtime STA configuration and verifies the read-back. */
esp_err_t wifi_station_save_credentials(const uint8_t *ssid, size_t ssid_length,
                                        const uint8_t *password, size_t password_length);

/* Development USB operation: clears only the dedicated Gateway credential. */
esp_err_t wifi_station_clear_saved_credentials(void);
esp_err_t wifi_station_has_saved_credentials(bool *configured);

/* Development setter-image helper. It persists the supplied Gateway
 * credential through the OMK credential store and does not modify runtime
 * STA configuration. */
esp_err_t wifi_station_set_saved_credentials_for_development(
    const uint8_t *ssid, size_t ssid_length,
    const uint8_t *password, size_t password_length);
