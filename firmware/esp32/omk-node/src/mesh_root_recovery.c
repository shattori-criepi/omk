#include "mesh_root_recovery.h"

#include <stddef.h>
#include <string.h>

static bool time_reached(uint32_t now_ms, uint32_t deadline_ms) {
    return (int32_t)(now_ms - deadline_ms) >= 0;
}

void mesh_root_recovery_init(omk_mesh_root_recovery_t *state) {
    if (state != NULL) memset(state, 0, sizeof(*state));
}

void mesh_root_recovery_note_topology_change(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                              bool is_root) {
    if (state == NULL || !is_root || (state->has_executed &&
                                     !time_reached(now_ms, state->cooldown_until_ms))) return;
    /* Each new join resets the quiet period, coalescing a batch of joins. */
    state->pending = true;
    state->reason = OMK_MESH_ROOT_REELECTION_TOPOLOGY_CHANGE;
    state->due_ms = now_ms + OMK_MESH_ROOT_TOPOLOGY_STABILIZATION_MS;
}

void mesh_root_recovery_observe_link(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                     bool is_root, bool rssi_valid, int rssi_dbm,
                                     uint32_t parent_disconnect_count,
                                     uint32_t mqtt_disconnect_count) {
    if (state == NULL) return;
    if (!state->counters_initialized) {
        state->last_parent_disconnect_count = parent_disconnect_count;
        state->last_mqtt_disconnect_count = mqtt_disconnect_count;
        state->counters_initialized = true;
        return;
    }
    uint32_t parent_delta = parent_disconnect_count - state->last_parent_disconnect_count;
    uint32_t mqtt_delta = mqtt_disconnect_count - state->last_mqtt_disconnect_count;
    state->last_parent_disconnect_count = parent_disconnect_count;
    state->last_mqtt_disconnect_count = mqtt_disconnect_count;
    if (!is_root || !rssi_valid || rssi_dbm > OMK_MESH_ROOT_WEAK_RSSI_DBM ||
        (state->has_executed && !time_reached(now_ms, state->cooldown_until_ms))) return;
    /* RSSI alone is not sufficient: these conservative 30-second deltas require
     * observed disruption as well.  Parent churn is more direct; MQTT churn
     * catches an unstable root uplink that stays associated. */
    if (parent_delta < OMK_MESH_ROOT_PARENT_DISCONNECT_DELTA &&
        mqtt_delta < OMK_MESH_ROOT_MQTT_DISCONNECT_DELTA) return;
    state->pending = true;
    state->reason = OMK_MESH_ROOT_REELECTION_ROOT_LINK_UNHEALTHY;
    state->due_ms = now_ms;
}

bool mesh_root_recovery_should_execute(const omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                       bool is_root, bool parent_connected, bool rootless) {
    return state != NULL && state->pending && is_root && parent_connected && !rootless &&
           (!state->has_executed || time_reached(now_ms, state->cooldown_until_ms)) &&
           time_reached(now_ms, state->due_ms);
}

void mesh_root_recovery_mark_executed(omk_mesh_root_recovery_t *state, uint32_t now_ms) {
    if (state == NULL) return;
    state->pending = false;
    state->has_executed = true;
    state->last_execution_ms = now_ms;
    state->cooldown_until_ms = now_ms + OMK_MESH_ROOT_REELECTION_COOLDOWN_MS;
}

bool mesh_root_recovery_in_grace(const omk_mesh_root_recovery_t *state, uint32_t now_ms) {
    return state != NULL && state->has_executed &&
           !time_reached(now_ms, state->last_execution_ms + OMK_MESH_ROOT_REELECTION_GRACE_MS);
}

const char *mesh_root_recovery_reason_name(omk_mesh_root_reelection_reason_t reason) {
    switch (reason) {
    case OMK_MESH_ROOT_REELECTION_TOPOLOGY_CHANGE: return "topology_change";
    case OMK_MESH_ROOT_REELECTION_ROOT_LINK_UNHEALTHY: return "root_link_unhealthy";
    default: return "none";
    }
}
