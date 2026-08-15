#pragma once

#include <stdbool.h>
#include "esp_err.h"

#define NODE_NVS_NAMESPACE "omk"
#define NODE_NVS_PROVISIONING_POP_KEY "prov_pop"
#define NODE_NVS_NEXT_BOOT_MODE_KEY "next_boot_mode"
#define NODE_PROVISIONING_POP_LENGTH 32

typedef enum {
    NODE_STATE_DISCOVERY,
    NODE_STATE_PROVISIONING_BOOT_PENDING,
} node_state_t;

typedef enum {
    NODE_EVENT_START_PROVISIONING,
} node_event_t;

typedef enum {
    NODE_BOOT_MODE_DEFAULT = 0,
    NODE_BOOT_MODE_PROVISIONING = 1,
} node_boot_mode_t;

esp_err_t node_state_start(void);
bool node_state_post_event(node_event_t event);
