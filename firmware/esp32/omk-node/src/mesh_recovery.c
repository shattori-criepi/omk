#include "mesh_recovery.h"

#include <limits.h>
#include <string.h>

static void increment(uint32_t *n) { if (*n < UINT32_MAX) ++*n; }
static bool elapsed(uint32_t now, uint32_t since, uint32_t duration) {
    return (uint32_t)(now - since) >= duration;
}

void mesh_recovery_init(omk_mesh_recovery_t *s, uint64_t node_id) {
    memset(s, 0, sizeof(*s));
    /* Preserve the existing self-heal/reselection staggering exactly. */
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
    else if (lost) s->stage = s->explicit_accepted ? OMK_RECOVERY_EXPLICIT : OMK_RECOVERY_SELF_HEALING;
    else if (healthy) s->stage = OMK_RECOVERY_HEALTHY;
    else if (!in->has_ip) s->stage = OMK_RECOVERY_WAIT_IP;
    else {
        s->stage = OMK_RECOVERY_MQTT_WAIT;
        s->reason = OMK_RECOVERY_MQTT_ONLY;
    }
    /* Keep the last recovery reason for post-recovery diagnostics. */
}

omk_mesh_recovery_action_t mesh_recovery_choose(omk_mesh_recovery_t *s, uint32_t now,
    const omk_mesh_recovery_input_t *in, omk_mesh_root_recovery_t *root) {
    mesh_recovery_observe(s, now, in);
    if (!in->started) return OMK_RECOVERY_NONE;
    /* A started child IP deadline survives parent flaps and is evaluated even
     * on disconnected monitor ticks. Initial discovery has no IP deadline. */
    if (s->ip_wait_active) {
        s->reason = OMK_RECOVERY_IP_TIMEOUT;
        if (mesh_root_recovery_in_grace(root, now)) return OMK_RECOVERY_NONE;
        if (elapsed(now, s->ip_since_ms, OMK_MESH_IP_RESELECT_MS) &&
            (!s->attempted || elapsed(now, s->attempt_ms, OMK_MESH_RESELECT_INTERVAL_MS)))
            return OMK_RECOVERY_CHILD_RESELECT;
        return OMK_RECOVERY_NONE;
    }
    if (s->loss_active) {
        if (mesh_root_recovery_in_grace(root, now)) return OMK_RECOVERY_NONE;
        if (!elapsed(now, s->loss_since_ms, OMK_MESH_SELF_HEAL_MS + s->jitter_ms / 2))
            return OMK_RECOVERY_NONE;
        if (s->attempted && s->explicit_accepted &&
            !elapsed(now, s->attempt_ms, OMK_MESH_RESELECT_INTERVAL_MS + s->jitter_ms / 2))
            return OMK_RECOVERY_NONE;
        /* A root with an intact router association must not be demoted merely
         * because NETWORK_STATE temporarily reports rootless. */
        if (in->is_root && in->parent_connected) return OMK_RECOVERY_NONE;
        return in->is_root ? OMK_RECOVERY_ROUTER_RECONNECT : OMK_RECOVERY_CHILD_RESELECT;
    }
    if (mesh_root_recovery_should_execute(root, now, in->is_root, in->parent_connected, in->rootless))
        return OMK_RECOVERY_ROOT_VOTE;
    return OMK_RECOVERY_NONE;
}

void mesh_recovery_complete(omk_mesh_recovery_t *s, uint32_t now,
                            omk_mesh_recovery_action_t action, bool success) {
    if (action == OMK_RECOVERY_CHILD_RESELECT || action == OMK_RECOVERY_ROUTER_RECONNECT) {
        s->attempted = true;
        s->attempt_ms = now;
        if (success) {
            s->explicit_accepted = true;
            s->stage = OMK_RECOVERY_EXPLICIT;
            if (action == OMK_RECOVERY_CHILD_RESELECT) increment(&s->parent_reselection_count);
        }
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
