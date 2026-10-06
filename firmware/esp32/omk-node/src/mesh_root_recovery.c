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
    /* Observe Level 2 from the first sample, even during Level 1 cooldown.
     * A vote alone does not break a continuously weak root link. */
    if (!is_root || !rssi_valid || rssi_dbm > OMK_MESH_ROOT_SEVERE_WEAK_RSSI_DBM) {
        state->severe_weak_active = false;
        state->severe_weak_since_ms = 0;
    } else if (!state->severe_weak_active) {
        state->severe_weak_active = true;
        state->severe_weak_since_ms = now_ms;
    }
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
    /* Level 1 requires disruption as well as weak RSSI. Parent churn is more
     * direct; MQTT churn catches an unstable root uplink that stays associated. */
    if (parent_delta < OMK_MESH_ROOT_PARENT_DISCONNECT_DELTA &&
        mqtt_delta < OMK_MESH_ROOT_MQTT_DISCONNECT_DELTA) return;
    state->pending = true;
    state->reason = OMK_MESH_ROOT_REELECTION_ROOT_LINK_UNHEALTHY;
    state->due_ms = now_ms;
}

omk_mesh_root_recovery_action_t mesh_root_recovery_should_execute(
    const omk_mesh_root_recovery_t *state, uint32_t now_ms,
    bool is_root, bool parent_connected, bool rootless) {
    if (state == NULL || !is_root || !parent_connected || rootless)
        return OMK_MESH_ROOT_RECOVERY_NONE;
    if (state->severe_weak_active &&
        (uint32_t)(now_ms - state->severe_weak_since_ms) >= OMK_MESH_ROOT_SEVERE_WEAK_DURATION_MS &&
        (!state->has_parent_reselected ||
         (uint32_t)(now_ms - state->last_parent_reselection_ms) >=
             OMK_MESH_ROOT_PARENT_RESELECTION_COOLDOWN_MS)) {
        return OMK_MESH_ROOT_RECOVERY_PARENT_RESELECTION;
    }
    if (state->pending &&
        (!state->has_executed || time_reached(now_ms, state->cooldown_until_ms)) &&
        time_reached(now_ms, state->due_ms)) {
        return OMK_MESH_ROOT_RECOVERY_REELECTION;
    }
    return OMK_MESH_ROOT_RECOVERY_NONE;
}

void mesh_root_recovery_mark_executed(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                     omk_mesh_root_recovery_action_t action) {
    if (state == NULL || action == OMK_MESH_ROOT_RECOVERY_NONE) return;
    /* Both successful actions defer further votes and grant liveness grace.
     * Only Level 2 starts its own cooldown and resets the weak observation. */
    state->pending = false;
    state->has_executed = true;
    state->last_execution_ms = now_ms;
    state->cooldown_until_ms = now_ms + OMK_MESH_ROOT_REELECTION_COOLDOWN_MS;
    if (action == OMK_MESH_ROOT_RECOVERY_PARENT_RESELECTION) {
        state->has_parent_reselected = true;
        state->last_parent_reselection_ms = now_ms;
        state->severe_weak_active = false;
        state->severe_weak_since_ms = 0;
    }
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
