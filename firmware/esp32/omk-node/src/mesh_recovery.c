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
    bool role_changed = !s->role_known || s->last_is_root != in->is_root;
    bool was_loss = s->loss_active, was_ip_wait = s->ip_wait_active;
    bool was_started = s->last_started;
    if (s->role_known && role_changed) {
        /* A child's accepted reselect is not a Root router reconnect (or the
         * reverse). Start the new role's isolation episode with full grace. */
        s->loss_active = false;
        s->explicit_accepted = false;
        s->attempted = false;
    }
    s->role_known = true;
    s->last_is_root = in->is_root;
    s->last_started = in->started;
    bool healthy = in->started && in->parent_connected && !in->rootless &&
                   in->has_ip && in->mqtt_connected && in->mqtt_progress;
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
        if (in->has_ip || in->is_root) {
            s->explicit_accepted = false;
            s->attempted = false;
        }
    }

    bool mqtt_wait = in->started && !lost && in->has_ip && in->mqtt_started &&
                     (!in->mqtt_connected || !in->mqtt_progress);
    if (!mqtt_wait) s->mqtt_wait_active = false;
    else if (!s->mqtt_wait_active) {
        s->mqtt_wait_active = true;
        s->mqtt_since_ms = now;
    }
    /* Keep the deadline across our own reselection's parent disconnect. Start
     * it only once a parent has existed, so initial network search gets grace. */
    bool ip_wait = in->started && !in->is_root && !in->has_ip &&
                   (!lost || s->ip_wait_active);
    if (!ip_wait) s->ip_wait_active = false;
    else if (!s->ip_wait_active) {
        s->ip_wait_active = true;
        s->ip_since_ms = now;
    }
    /* An IP-wait episode includes its short parent flaps. Other loss episodes
     * end when the parent returns. A recovered/new episode must reject old APIs. */
    if (role_changed || was_started != in->started || was_ip_wait != s->ip_wait_active ||
        (!s->ip_wait_active && was_loss != s->loss_active)) ++s->generation;
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
    const omk_mesh_recovery_input_t *in, omk_mesh_root_recovery_t *root) {
    mesh_recovery_observe(s, now, in);
    if (!in->started) return OMK_RECOVERY_NONE;
    if (!s->retry_clock_started) {
        s->retry_clock_started = true;
        s->retry_since_ms = now;
    }
    if (elapsed(now, s->retry_since_ms, OMK_MESH_RESTART_RETRY_MS + s->jitter_ms))
        s->retry_ready = true; /* Latch across uint32 timer wrap. */
    bool restart_allowed = !s->persistence_blocked && (!s->restart_spent || s->retry_ready);
    /* A started child IP deadline survives parent flaps and is evaluated even
     * on disconnected monitor ticks. Initial discovery has no IP deadline. */
    if (s->ip_wait_active) {
        s->reason = OMK_RECOVERY_IP_TIMEOUT;
        if (mesh_root_recovery_in_grace(root, now)) return OMK_RECOVERY_NONE;
        if (restart_allowed && elapsed(now, s->ip_since_ms, OMK_MESH_IP_RESTART_MS + s->jitter_ms)) {
            s->stage = OMK_RECOVERY_RESTART_PENDING;
            return OMK_RECOVERY_RESTART;
        }
        if (elapsed(now, s->ip_since_ms, OMK_MESH_IP_RESELECT_MS) &&
            (!s->attempted || elapsed(now, s->attempt_ms, OMK_MESH_RESELECT_INTERVAL_MS)))
            return OMK_RECOVERY_CHILD_RESELECT;
        return OMK_RECOVERY_NONE;
    }
    if (s->loss_active) {
        if (mesh_root_recovery_in_grace(root, now)) return OMK_RECOVERY_NONE;
        if (!elapsed(now, s->loss_since_ms, OMK_MESH_SELF_HEAL_MS + s->jitter_ms / 2))
            return OMK_RECOVERY_NONE;
        bool child_control_stalled = !in->is_root && in->control_stalled;
        if (restart_allowed && !(in->is_root && in->parent_connected) &&
            (s->explicit_accepted || child_control_stalled) &&
            elapsed(now, s->loss_since_ms, OMK_MESH_ISOLATION_RESTART_MS + s->jitter_ms) &&
            (child_control_stalled || elapsed(now, s->explicit_since_ms, OMK_MESH_RECONNECT_SETTLE_MS))) {
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
    if (mesh_root_recovery_should_execute(root, now, in->is_root, in->parent_connected, in->rootless))
        return OMK_RECOVERY_ROOT_VOTE;
    omk_mesh_mqtt_liveness_state_t mqtt = {
        .mesh_started = in->started, .parent_connected = in->parent_connected,
        .rootless = in->rootless, .has_ip = in->has_ip, .normal_operation = in->mqtt_started,
        .mqtt_connected = in->mqtt_connected,
        .mqtt_progress = in->mqtt_progress,
        .mqtt_disconnected_duration_s = s->mqtt_wait_active ? (now - s->mqtt_since_ms) / 1000U : 0,
    };
    if (!in->is_root && restart_allowed && !mesh_root_recovery_in_grace(root, now) &&
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
        s->persistence_blocked = !success;
        s->retry_clock_started = true;
        s->retry_since_ms = now;
        s->retry_ready = false;
        s->stage = OMK_RECOVERY_BUDGET_HOLD;
    } else if (action == OMK_RECOVERY_REARM && success) {
        s->restart_spent = false;
        s->persistence_blocked = false;
        s->retry_since_ms = now;
        s->retry_ready = false;
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
    case OMK_RECOVERY_ROOT_LINK: return "root_link_unhealthy";
    case OMK_RECOVERY_IP_TIMEOUT: return "ip_timeout";
    default: return "none";
    }
}

bool mesh_recovery_restart_valid(const omk_mesh_recovery_t *selected,
    const omk_mesh_recovery_t *current, uint32_t now,
    const omk_mesh_recovery_input_t *in, const omk_mesh_root_recovery_t *root,
    bool selected_root) {
    if (in->is_root != selected_root || current->generation != selected->generation) return false;
    omk_mesh_recovery_t check = *current;
    omk_mesh_root_recovery_t root_check = *root;
    if (mesh_recovery_choose(&check, now, in, &root_check) != OMK_RECOVERY_RESTART)
        return false;
    if (selected->reason == OMK_RECOVERY_IP_TIMEOUT)
        return check.reason == selected->reason && check.ip_wait_active &&
               check.ip_since_ms == selected->ip_since_ms;
    return check.reason == selected->reason &&
        check.loss_active == selected->loss_active &&
        check.loss_since_ms == selected->loss_since_ms &&
        check.mqtt_wait_active == selected->mqtt_wait_active &&
        check.mqtt_since_ms == selected->mqtt_since_ms &&
        check.ip_wait_active == selected->ip_wait_active &&
        check.ip_since_ms == selected->ip_since_ms;
}

omk_mesh_recovery_token_t mesh_recovery_action_token(const omk_mesh_recovery_t *s,
                                                     omk_mesh_recovery_action_t action) {
    return (omk_mesh_recovery_token_t){.action=action, .generation=s->generation,
                                      .is_root=s->last_is_root};
}

bool mesh_recovery_action_matches(const omk_mesh_recovery_t *s,
                                  const omk_mesh_recovery_token_t *token) {
    if (!token || !s->last_started || token->generation != s->generation ||
        token->is_root != s->last_is_root) return false;
    if (token->action == OMK_RECOVERY_ROOT_VOTE) return s->last_is_root;
    if (token->action == OMK_RECOVERY_ROUTER_RECONNECT)
        return s->last_is_root && s->loss_active;
    return token->action == OMK_RECOVERY_CHILD_RESELECT && !s->last_is_root &&
           (s->loss_active || s->ip_wait_active);
}
