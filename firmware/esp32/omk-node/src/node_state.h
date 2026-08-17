#pragma once

#include <stdbool.h>
#include "esp_err.h"

#define NODE_NVS_NAMESPACE "omk"
#define NODE_NVS_PROVISIONING_POP_KEY "prov_pop"
#define NODE_PROVISIONING_POP_LENGTH 32

/* Development reset images use this to read back (without logging) the
 * node-specific PoP before and after clearing unrelated Wi-Fi credentials. */
esp_err_t node_state_verify_provisioning_pop(void);
