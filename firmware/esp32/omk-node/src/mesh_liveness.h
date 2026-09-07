#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Keep this policy independent of ESP-IDF so its boundary conditions can be
 * verified on the host. */
#define OMK_MESH_MQTT_LIVENESS_TIMEOUT_S 180U

typedef struct {
    bool mesh_started;
    bool parent_connected;
    bool rootless;
    bool has_ip;
    bool normal_operation;
    bool mqtt_connected;
    uint32_t mqtt_disconnected_duration_s;
} omk_mesh_mqtt_liveness_state_t;

bool mesh_mqtt_liveness_should_recover(const omk_mesh_mqtt_liveness_state_t *state);
