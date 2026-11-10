#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define OMK_NODE_LOGICAL_ID_MAX_LENGTH 48

/* NULL persists a clear tombstone. Stale or conflicting same-revision commands
 * return ESP_ERR_INVALID_STATE; identical commands do not write flash. */
esp_err_t node_registration_apply(uint32_t revision, const char *logical_id);
uint32_t node_registration_get_revision(void);
/* One coherent RAM snapshot. Empty ID means cleared; errors propagate. */
esp_err_t node_registration_snapshot(char *logical_id, size_t size, uint32_t *revision);

/* Local/USB compatibility operations increment the persisted revision. */
esp_err_t node_registration_save(const char *logical_id);

/* Load once after NVS/factory initialization, before concurrent readers. */
void node_registration_init_cache(void);

/* Read the verified RAM cache; never waits for NVS/flash. */
esp_err_t node_registration_get_logical_id(char *logical_id, size_t size);

/* Persist a clear tombstone at the next revision. Wi-Fi credentials remain. */
esp_err_t node_registration_clear(void);

/* Development-flash compatibility wrapper for node_registration_clear(). */
esp_err_t node_registration_clear_for_development(void);

/* Determines the Discovery v1 state without initializing a Wi-Fi driver. */
esp_err_t node_registration_get_provisioning_state(bool has_wifi_credentials,
                                                   uint8_t *state);
