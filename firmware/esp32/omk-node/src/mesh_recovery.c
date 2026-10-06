#include "mesh_recovery.h"

#include <limits.h>
#include <string.h>
#include "mesh_liveness.h"

static void increment(uint32_t *n) { if (*n < UINT32_MAX) ++*n; }
static bool elapsed(uint32_t now, uint32_t since, uint32_t duration) {
    return (uint32_t)(now - since) >= duration;
}

void mesh_recovery_init(omk_mesh_recovery_t *s, bool spent, uint64_t node_id) {
    memset(s, 0, sizeof(*s));
    s->restart_spent = spent;
    /* Hash every ID byte; restart offsets span four 30-second status slots. */
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < 8; ++i) {
        hash = (hash ^ (uint8_t)node_id) * 16777619U;
        node_id >>= 8;
    }
    s->jitter_ms = hash % 120001U;
}

void mesh_recovery_note_no_parent(omk_mesh_recovery_t *s, int scan_times) {
    increment(&s->no_parent_found_count);
    s->last_scan_times = scan_times;
    if (s->loss_active) s->reason = OMK_RECOVERY_NO_PARENT;
}

void mesh_recovery_note_stopped(omk_mesh_recovery_t *s) {
    increment(&s->stop_reconnection_count);
    if (s->loss_active) s->reason = OMK_RECOVERY_STOPPED;
}

void mesh_recovery_observe(omk_mesh_recovery_t *s, uint32_t now,
                           const omk_mesh_recovery_input_t *in) {
    bool healthy = in->started && in->parent_connected && !in->rootless &&
                   in->has_ip && in->mqtt_connected;
    if (!healthy || s->mqtt_disconnect_count != in->mqtt_disconnect_count)
        s->healthy_active = false;
    s->mqtt_disconnect_count = in->mqtt_disconnect_count;
    if (healthy && !s->healthy_active) {
        s->healthy_active = true;
        s->healthy_since_ms = now;
    }

    bool lost = in->started && (!in->parent_connected || in->rootless);
    if (lost && !s->loss_active) {
        s->loss_active = true;
        s->loss_since_ms = now;
        s->reason = in->is_root ? OMK_RECOVERY_ROUTER_LOSS :
                    in->rootless ? OMK_RECOVERY_ROOTLESS : OMK_RECOVERY_PARENT_LOSS;
    } else if (!lost) {
        s->loss_active = false;
        s->explicit_accepted = false;
        s->attempted = false;
    }

    bool mqtt_wait = in->started && !lost && in->has_ip && in->mqtt_started &&
                     !in->mqtt_connected;
    if (!mqtt_wait) s->mqtt_wait_active = false;
    else if (!s->mqtt_wait_active) {
        s->mqtt_wait_active = true;
        s->mqtt_since_ms = now;
    }
    if (!in->started) s->stage = OMK_RECOVERY_IDLE;
    else if (lost) s->stage = s->restart_spent ? OMK_RECOVERY_BUDGET_HOLD :
                             s->explicit_accepted ? OMK_RECOVERY_EXPLICIT : OMK_RECOVERY_SELF_HEALING;
    else if (healthy) s->stage = OMK_RECOVERY_HEALTHY;
    else if (!in->has_ip) s->stage = OMK_RECOVERY_WAIT_IP;
    else {
        s->stage = s->restart_spent ? OMK_RECOVERY_BUDGET_HOLD : OMK_RECOVERY_MQTT_WAIT;
        s->reason = OMK_RECOVERY_MQTT_ONLY;
    }
    /* Keep the last recovery reason for post-recovery diagnostics. */
}

omk_mesh_recovery_action_t mesh_recovery_choose(omk_mesh_recovery_t *s, uint32_t now,
    const omk_mesh_recovery_input_t *in, const omk_mesh_root_recovery_t *root) {
    mesh_recovery_observe(s, now, in);
    if (!in->started) return OMK_RECOVERY_NONE;
    if (s->loss_active) {
        if (mesh_root_recovery_in_grace(root, now)) return OMK_RECOVERY_NONE;
        if (!elapsed(now, s->loss_since_ms, OMK_MESH_SELF_HEAL_MS + s->jitter_ms / 2))
            return OMK_RECOVERY_NONE;
        if (!s->restart_spent && s->explicit_accepted &&
            elapsed(now, s->loss_since_ms, OMK_MESH_ISOLATION_RESTART_MS + s->jitter_ms) &&
            elapsed(now, s->explicit_since_ms, OMK_MESH_RECONNECT_SETTLE_MS)) {
            s->stage = OMK_RECOVERY_RESTART_PENDING;
            return OMK_RECOVERY_RESTART;
        }
        if (s->attempted && (s->explicit_accepted || s->restart_spent) &&
            !elapsed(now, s->attempt_ms, OMK_MESH_RESELECT_INTERVAL_MS + s->jitter_ms / 2))
            return OMK_RECOVERY_NONE;
        /* A root with an intact router association must not be demoted merely
         * because NETWORK_STATE temporarily reports rootless. */
        if (in->is_root && in->parent_connected) return OMK_RECOVERY_NONE;
        return in->is_root ? OMK_RECOVERY_ROUTER_RECONNECT : OMK_RECOVERY_CHILD_RESELECT;
    }
    omk_mesh_root_recovery_action_t root_action = mesh_root_recovery_should_execute(
        root, now, in->is_root, in->parent_connected, in->rootless);
    if (root_action == OMK_MESH_ROOT_RECOVERY_PARENT_RESELECTION) return OMK_RECOVERY_WEAK_ROOT;
    if (root_action == OMK_MESH_ROOT_RECOVERY_REELECTION) return OMK_RECOVERY_ROOT_VOTE;
    omk_mesh_mqtt_liveness_state_t mqtt = {
        .mesh_started = in->started, .parent_connected = in->parent_connected,
        .rootless = in->rootless, .has_ip = in->has_ip, .normal_operation = in->mqtt_started,
        .mqtt_connected = in->mqtt_connected,
        .mqtt_disconnected_duration_s = s->mqtt_wait_active ? (now - s->mqtt_since_ms) / 1000U : 0,
    };
    if (!s->restart_spent && !mesh_root_recovery_in_grace(root, now) &&
        mesh_mqtt_liveness_should_recover(&mqtt) &&
        elapsed(now, s->mqtt_since_ms, OMK_MESH_MQTT_LIVENESS_TIMEOUT_S * 1000U + s->jitter_ms)) {
        s->stage = OMK_RECOVERY_RESTART_PENDING;
        s->reason = OMK_RECOVERY_MQTT_ONLY;
        return OMK_RECOVERY_RESTART;
    }
    if (s->restart_spent && s->healthy_active &&
        elapsed(now, s->healthy_since_ms, OMK_MESH_HEALTHY_REARM_MS)) return OMK_RECOVERY_REARM;
    return OMK_RECOVERY_NONE;
}

void mesh_recovery_complete(omk_mesh_recovery_t *s, uint32_t now,
                            omk_mesh_recovery_action_t action, bool success) {
    if (action == OMK_RECOVERY_CHILD_RESELECT || action == OMK_RECOVERY_ROUTER_RECONNECT) {
        s->attempted = true;
        s->attempt_ms = now;
        if (success) {
            s->explicit_since_ms = now;
            s->explicit_accepted = true;
            s->stage = s->restart_spent ? OMK_RECOVERY_BUDGET_HOLD : OMK_RECOVERY_EXPLICIT;
            if (action == OMK_RECOVERY_CHILD_RESELECT) increment(&s->parent_reselection_count);
        }
    } else if (action == OMK_RECOVERY_RESTART) {
        /* Persistence failure closes the budget in RAM too. Never reboot when
         * the durable marker cannot be verified. */
        s->restart_spent = true;
        s->stage = OMK_RECOVERY_BUDGET_HOLD;
    } else if (action == OMK_RECOVERY_REARM && success) {
        s->restart_spent = false;
    }
}

uint32_t mesh_recovery_loss_duration_s(const omk_mesh_recovery_t *s, uint32_t now) {
    return s->loss_active ? (now - s->loss_since_ms) / 1000U : 0;
}

const char *mesh_recovery_stage_name(omk_mesh_recovery_stage_t stage) {
    switch (stage) {
    case OMK_RECOVERY_HEALTHY: return "healthy";
    case OMK_RECOVERY_SELF_HEALING: return "self_healing";
    case OMK_RECOVERY_EXPLICIT: return "explicit";
    case OMK_RECOVERY_WAIT_IP: return "wait_ip";
    case OMK_RECOVERY_MQTT_WAIT: return "mqtt_wait";
    case OMK_RECOVERY_BUDGET_HOLD: return "budget_hold";
    case OMK_RECOVERY_RESTART_PENDING: return "restart_pending";
    default: return "idle";
    }
}

const char *mesh_recovery_reason_name(omk_mesh_recovery_reason_t reason) {
    switch (reason) {
    case OMK_RECOVERY_PARENT_LOSS: return "parent_loss";
    case OMK_RECOVERY_ROOTLESS: return "rootless";
    case OMK_RECOVERY_ROUTER_LOSS: return "router_loss";
    case OMK_RECOVERY_NO_PARENT: return "no_parent_found";
    case OMK_RECOVERY_STOPPED: return "stop_reconnection";
    case OMK_RECOVERY_MQTT_ONLY: return "mqtt_only";
    case OMK_RECOVERY_SEVERE_ROOT: return "sustained_weak_root";
    case OMK_RECOVERY_TOPOLOGY: return "topology_change";
    case OMK_RECOVERY_ROOT_LINK: return "root_link_unhealthy";
    default: return "none";
    }
}
