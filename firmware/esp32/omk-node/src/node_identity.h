#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define OMK_NODE_ID_HEX_LENGTH 12

esp_err_t node_identity_get_id(uint64_t *node_id);
esp_err_t node_identity_get_id_hex(char *node_id, size_t size);
