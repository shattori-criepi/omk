#include "mesh_root_recovery.h"

#include <stddef.h>
#include <string.h>

static void expire_timers(omk_mesh_root_recovery_t *state, uint32_t now_ms) {
    uint32_t elapsed_ms = now_ms - state->last_execution_ms;
    /* Clearing the flags is essential: a stale timestamp alone becomes recent
     * again after a full uint32 wrap. Only an accepted vote rearms the timers. */
    if (elapsed_ms >= OMK_MESH_ROOT_REELECTION_GRACE_MS) state->grace_active = false;
    if (elapsed_ms >= OMK_MESH_ROOT_REELECTION_COOLDOWN_MS) state->cooldown_active = false;
}

void mesh_root_recovery_init(omk_mesh_root_recovery_t *state) {
    if (state != NULL) memset(state, 0, sizeof(*state));
}

void mesh_root_recovery_observe_link(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                     bool is_root, bool rssi_valid, int rssi_dbm,
                                     uint32_t parent_disconnect_count,
                                     uint32_t mqtt_disconnect_count) {
    if (state == NULL) return;
    expire_timers(state, now_ms);
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
        state->cooldown_active) return;
    /* Level 1 requires disruption as well as weak RSSI. Parent churn is more
     * direct; MQTT churn catches an unstable root uplink that stays associated. */
    if (parent_delta < OMK_MESH_ROOT_PARENT_DISCONNECT_DELTA &&
        mqtt_delta < OMK_MESH_ROOT_MQTT_DISCONNECT_DELTA) return;
    state->pending = true;
}

bool mesh_root_recovery_should_execute(
    omk_mesh_root_recovery_t *state, uint32_t now_ms,
    bool is_root, bool parent_connected, bool rootless) {
    if (state == NULL) return false;
    expire_timers(state, now_ms);
    return is_root && parent_connected && !rootless && state->pending && !state->cooldown_active;
}

void mesh_root_recovery_mark_executed(omk_mesh_root_recovery_t *state, uint32_t now_ms) {
    if (state == NULL) return;
    state->pending = false;
    state->last_execution_ms = now_ms;
    state->cooldown_active = true;
    state->grace_active = true;
}

bool mesh_root_recovery_in_grace(omk_mesh_root_recovery_t *state, uint32_t now_ms) {
    if (state == NULL) return false;
    expire_timers(state, now_ms);
    return state->grace_active;
}
