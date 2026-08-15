#pragma once

#include <stdbool.h>

#include "esp_err.h"

/* Starts a station connection only when ESP-IDF has persisted a STA SSID.
 * A node without Wi-Fi credentials returns ESP_OK and remains in discovery. */
esp_err_t wifi_station_start_if_provisioned(bool *has_saved_credentials);
