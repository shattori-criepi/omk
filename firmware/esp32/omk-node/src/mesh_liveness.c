#include "mesh_liveness.h"

#include <stddef.h>

bool mesh_mqtt_liveness_should_recover(const omk_mesh_mqtt_liveness_state_t *state) {
    return state != NULL && state->mesh_started && state->parent_connected &&
           !state->rootless && state->has_ip && state->normal_operation &&
           !state->mqtt_connected &&
           state->mqtt_disconnected_duration_s >= OMK_MESH_MQTT_LIVENESS_TIMEOUT_S;
}
