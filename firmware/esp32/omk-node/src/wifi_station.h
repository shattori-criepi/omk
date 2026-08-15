#pragma once

#include <stdbool.h>

#include "esp_err.h"

/* Initializes shared netif/event loop/Wi-Fi state and checks the persisted
 * STA configuration. The caller then starts either STA or provisioning. */
esp_err_t wifi_station_prepare(bool *has_saved_credentials);
esp_err_t wifi_station_start_prepared(void);
