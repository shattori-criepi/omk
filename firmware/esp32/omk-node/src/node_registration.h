#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define OMK_NODE_LOGICAL_ID_MAX_LENGTH 48

/* Persists logical_id and registered=1, commits, and verifies the values by
 * reading them back. */
esp_err_t node_registration_save(const char *logical_id);

/* Determines the Discovery v1 state without initializing a Wi-Fi driver. */
esp_err_t node_registration_get_provisioning_state(bool has_wifi_credentials,
                                                   uint8_t *state);
