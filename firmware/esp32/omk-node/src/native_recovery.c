#include "native_recovery.h"
#include <string.h>

void native_recovery_init(native_recovery_t *s, uint32_t jitter_ms) {
    memset(s, 0, sizeof(*s));
    s->jitter_ms = jitter_ms % 30001U;
}

static bool child_budget_available(const native_recovery_t *s, uint64_t now) {
    return now >= s->mesh_after && (s->tx_attempt_count < NATIVE_RECOVERY_TX_ATTEMPTS ||
           now - s->tx_attempt_ms[0] >= NATIVE_RECOVERY_TX_WINDOW_MS);
}

static void charge_child_budget(native_recovery_t *s, uint64_t now) {
    if (s->tx_attempt_count == NATIVE_RECOVERY_TX_ATTEMPTS) {
        memmove(s->tx_attempt_ms, s->tx_attempt_ms + 1,
                (NATIVE_RECOVERY_TX_ATTEMPTS - 1) * sizeof(s->tx_attempt_ms[0]));
        --s->tx_attempt_count;
    }
    s->tx_attempt_ms[s->tx_attempt_count++] = now;
    s->mesh_after = now + 300000 + s->jitter_ms;
}

void native_recovery_call_attempt(native_recovery_t *s, uint32_t call_id, uint64_t now) {
    if (s && s->call_seen && s->call_id == call_id && !s->call_attempted) {
        s->call_attempted = true;
        s->call_attempt_ms = now;
    }
}

native_recovery_action_t native_recovery_select(native_recovery_t *s,
    uint64_t now, const native_recovery_input_t *in) {
    s->child_causes = 0;
    if (!in->call_active || !s->call_seen || s->call_id != in->call_id) {
        s->call_seen = in->call_active;
        s->call_id = in->call_id;
        s->call_requested = s->call_attempted = false;
        s->call_attempt_ms = 0;
    }
    /* Observe from the independent owner, including while recovery is inhibited.
     * Idle producers must not look like a failed transport. A local success
     * restarts this local-stall clock, but says nothing about Root delivery. */
    if (!in->started || in->root || !in->parent || in->rootless || !in->tx_demand) {
        s->tx_watching = false;
    } else if (!s->tx_watching || s->tx_epoch != in->epoch || s->tx_progress != in->tx_progress ||
               now - s->tx_observed_ms > NATIVE_RECOVERY_TX_DEMAND_MS) {
        /* After a suspended owner, fresh demand is not evidence that demand
         * was continuous throughout the unobserved interval. */
        s->tx_watching = true;
        s->tx_since_ms = now;
    }
    s->tx_epoch = in->epoch;
    s->tx_progress = in->tx_progress;
    s->tx_observed_ms = now;
    /* Delivery progresses only on a fresh Root receipt. Use the original
     * status generation time, never SDK success or delayed receipt arrival. */
    if (!in->started || in->root || !in->parent || in->rootless || !in->delivery_demand) {
        s->delivery_watching = false;
    } else {
        if (!s->delivery_watching || s->delivery_epoch != in->epoch ||
            s->delivery_generation != in->delivery_generation ||
            now - s->delivery_observed_ms > NATIVE_RECOVERY_TX_DEMAND_MS) {
            s->delivery_watching = true;
            s->delivery_since_ms = now;
        }
        if (in->delivery_proven_ms > s->delivery_since_ms && in->delivery_proven_ms <= now)
            s->delivery_since_ms = in->delivery_proven_ms;
    }
    s->delivery_epoch = in->epoch;
    s->delivery_generation = in->delivery_generation;
    s->delivery_observed_ms = now;
    uint32_t condition = (in->root ? 1U : 0) | (in->parent ? 2U : 0) |
                         (in->rootless ? 4U : 0) | (in->ip ? 8U : 0);
    if (!s->initialized || s->epoch != in->epoch || s->condition != condition || !in->started) {
        s->initialized = true;
        s->epoch = in->epoch;
        s->condition = condition;
        s->episode_ms = now;
        s->light_attempted = false;
    }
    /* Connection transitions grant fresh SDK grace without forgetting an
     * earlier light recovery in the same physical link/progress episode. */
    if (s->mqtt_generation != in->mqtt_generation) {
        s->mqtt_generation = in->mqtt_generation;
        s->mqtt_since_ms = now;
    }
    bool call_due = in->call_active && now >= in->call_started_ms &&
                    now - in->call_started_ms >= NATIVE_RECOVERY_CALL_STALL_MS;
    if (call_due && !in->root) {
        /* A disconnected/rootless transition must not forgive the SAME call.
         * If B cannot run, start the final grace at the 60 s call deadline;
         * if it ran later, give it a full 120 s from the actual API attempt. */
        uint64_t since = s->call_attempted ? s->call_attempt_ms :
                         in->call_started_ms + NATIVE_RECOVERY_CALL_STALL_MS;
        if (in->restart_allowed && now >= since && now - since >= NATIVE_RECOVERY_CALL_RESTART_MS) {
            s->child_causes = REC_CHILD_TX_STALL | REC_CHILD_CALL_STALL;
            return REC_NODE_RESTART;
        }
        if (in->started && !in->voting && now >= in->inhibit_until && !in->recovery_busy &&
            !s->call_requested && child_budget_available(s, now)) {
            charge_child_budget(s, now);
            s->call_requested = true;
            s->child_causes = REC_CHILD_TX_STALL | REC_CHILD_CALL_STALL |
                              (in->quality_bad ? REC_CHILD_QUALITY : 0);
            return REC_CHILD_RESELECT;
        }
        /* Keep the relay's final-restart path unchanged. Other Nodes must
         * retain ordinary TX_STALL retries, even if the early API never ran.
         * The shared cooldown and hour budget also include the early request. */
        if (in->restart_allowed) return REC_NONE;
    }
    if (in->recovery_busy || !in->started || in->voting || now < in->inhibit_until) return REC_NONE;
    /* A completed natural reconnection is the safe opportunity to act on
     * repeated weak losses. Link epochs and individual PUBACKs are not proof
     * of sustained RF stability. Do not apply the continuous-outage grace
     * here: it would postpone recovery forever on a flapping link. */
    if (in->parent && !in->rootless && in->weak_parent_losses) {
        if (in->root && now >= s->vote_after) {
            s->vote_after = now + 900000 + s->jitter_ms;
            return REC_ROOT_VOTE;
        }
        if (!in->root && now >= s->mesh_after) {
            s->mesh_after = now + 300000 + s->jitter_ms;
            return REC_CHILD_RESELECT;
        }
    }
    if (now - s->episode_ms < 120000 + s->jitter_ms) return REC_NONE;
    unsigned causes = 0;
    if(in->quality_bad && !in->root && in->parent && !in->rootless)
        causes |= REC_CHILD_QUALITY;
    if (s->tx_watching && now - s->tx_since_ms >= NATIVE_RECOVERY_TX_STALL_MS + s->jitter_ms)
        causes |= REC_CHILD_TX_STALL;
    if (s->delivery_watching && now - s->delivery_since_ms >= NATIVE_RECOVERY_DELIVERY_STALL_MS + s->jitter_ms)
        causes |= REC_CHILD_DELIVERY_STALL;
    /* Do not spend the shared cooldown/budget on a QUALITY repair that can
     * only wait for the outstanding send. Keep observing TX until its normal
     * deadline permits parent reselection without waiting for that call. */
    if ((causes & REC_CHILD_QUALITY) && !(causes & REC_CHILD_TX_STALL) && in->sdk_busy)
        return REC_NONE;
    if (causes && child_budget_available(s, now)) {
        charge_child_budget(s, now);
        s->child_causes = causes;
        /* A stuck send cannot drain for REPAIR_QUIET, even with bad quality. */
        return (causes & REC_CHILD_TX_STALL) ? REC_CHILD_RESELECT :
               (causes & REC_CHILD_QUALITY) ? REC_CHILD_REPAIR : REC_CHILD_RESELECT;
    }
    if (now >= s->mesh_after) {
        native_recovery_action_t action = REC_NONE;
        if (!in->root && (!in->parent || in->rootless)) action = REC_CHILD_RESELECT;
        else if (in->root && !in->parent) action = REC_ROUTER_RECONNECT;
        else if (in->root && !in->rootless && !in->ip) action = REC_DHCP_START;
        if (action != REC_NONE) {
            s->mesh_after = now + 300000 + s->jitter_ms;
            return action;
        }
    }
    if (!in->root || !in->parent || in->rootless || !in->ip || !in->client)
        return REC_NONE;
    /* Keep the connection grace, but cap how far MQTT reconnections may
     * postpone an unresolved demand. Only a PUBACK closes that interval.
     * A disconnected client without demand retains its continuous budget. */
    if (in->mqtt && !in->qos1_pending) return REC_NONE;
    uint64_t since = in->mqtt ? in->demand_since_ms : in->disconnected_since_ms;
    if (since < s->episode_ms) since = s->episode_ms;
    if (in->mqtt && since < s->mqtt_since_ms) since = s->mqtt_since_ms;
    if (in->last_ack_ms > s->light_ms) s->light_attempted = false;
    bool overdue = in->qos1_pending &&
                   now - in->demand_since_ms >= NATIVE_RECOVERY_MQTT_BUDGET_MS + s->jitter_ms;
    if (!overdue && now - since < 120000 + s->jitter_ms) return REC_NONE;
    if (!s->light_attempted && in->mqtt) {
        s->light_attempted = true;
        s->light_ms = now;
        return REC_MQTT_RECONNECT;
    }
    bool exhausted = in->mqtt ? s->light_attempted && now - s->light_ms >= 120000 :
                               now - since >= 300000 + s->jitter_ms;
    /* Give a requested light recovery 120 seconds even if it disconnects.
     * Without a connected opportunity for that step, the cumulative budget
     * permits recreation directly. Reconnection never extends this deadline. */
    if (overdue)
        exhausted = s->light_attempted ? now - s->light_ms >= 120000 : !in->mqtt;
    if (exhausted && now >= s->recreate_after) {
        s->recreate_after = now + 900000 + s->jitter_ms;
        s->episode_ms = now;
        s->light_attempted = false;
        return REC_MQTT_RECREATE;
    }
    return REC_NONE;
}
