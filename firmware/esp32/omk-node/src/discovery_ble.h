#pragma once

#include <stdint.h>
#include "esp_err.h"

esp_err_t discovery_ble_start(uint64_t node_id, uint8_t provisioning_state);
/* Terminal for this boot: ESP-IDF does not allow controller init after deinit. */
esp_err_t discovery_ble_stop(void);
