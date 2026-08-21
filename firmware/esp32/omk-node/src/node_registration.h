#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define OMK_NODE_LOGICAL_ID_MAX_LENGTH 48

/* Persists logical_id and registered=1, commits, and verifies the values by
 * reading them back. */
esp_err_t node_registration_save(const char *logical_id);

/* Reads the registered logical ID from NVS. */
esp_err_t node_registration_get_logical_id(char *logical_id, size_t size);

/* Removes only omk/registered and omk/logical_id, commits once, then verifies
 * both keys are absent. Wi-Fi provisioning is deliberately retained. */
esp_err_t node_registration_clear(void);

/* Development-flash compatibility wrapper for node_registration_clear(). */
esp_err_t node_registration_clear_for_development(void);

/* Determines the Discovery v1 state without initializing a Wi-Fi driver. */
esp_err_t node_registration_get_provisioning_state(bool has_wifi_credentials,
                                                   uint8_t *state);
