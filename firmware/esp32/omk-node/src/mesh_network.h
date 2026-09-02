#pragma once

#include "esp_err.h"

#include <stdint.h>

/* Starts ESP-WIFI-MESH using the persisted STA credential as its router credential. */
esp_err_t mesh_network_start_prepared(void);
void mesh_network_log_diagnostics(const char *event, uint32_t reason);
