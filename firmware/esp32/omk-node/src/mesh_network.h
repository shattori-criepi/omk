#pragma once

#include "esp_err.h"

/* Starts ESP-WIFI-MESH using the persisted STA credential as its router credential. */
esp_err_t mesh_network_start_prepared(void);
