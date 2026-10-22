#pragma once

#include "mesh_root_recovery.h"

/* All durations are uint32_t milliseconds, below half the timer range. */
#define OMK_MESH_SELF_HEAL_MS 120000U
#define OMK_MESH_RECONNECT_SETTLE_MS 180000U
#define OMK_MESH_ISOLATION_RESTART_MS 600000U
#define OMK_MESH_RESELECT_INTERVAL_MS 300000U
#define OMK_MESH_HEALTHY_REARM_MS 900000U
/* A spent budget permits one retry per ten minutes of uptime, including after reboot.
 * It is NOT rearmed by time alone. NVS errors still fail closed. */
#define OMK_MESH_RESTART_RETRY_MS 600000U
#ifndef OMK_MESH_IP_RESELECT_MS
#define OMK_MESH_IP_RESELECT_MS 180000U
#endif
#ifndef OMK_MESH_IP_RESTART_MS
#define OMK_MESH_IP_RESTART_MS 600000U
#endif

typedef enum {
    OMK_RECOVERY_NONE, OMK_RECOVERY_CHILD_RESELECT, OMK_RECOVERY_ROUTER_RECONNECT,
    OMK_RECOVERY_ROOT_VOTE, OMK_RECOVERY_RESTART,
    OMK_RECOVERY_REARM,
} omk_mesh_recovery_action_t;

typedef enum {
    OMK_RECOVERY_IDLE, OMK_RECOVERY_HEALTHY, OMK_RECOVERY_SELF_HEALING,
    OMK_RECOVERY_EXPLICIT, OMK_RECOVERY_WAIT_IP, OMK_RECOVERY_MQTT_WAIT,
    OMK_RECOVERY_BUDGET_HOLD, OMK_RECOVERY_RESTART_PENDING,
} omk_mesh_recovery_stage_t;

typedef enum {
    OMK_RECOVERY_REASON_NONE, OMK_RECOVERY_PARENT_LOSS, OMK_RECOVERY_ROOTLESS,
    OMK_RECOVERY_ROUTER_LOSS, OMK_RECOVERY_NO_PARENT, OMK_RECOVERY_STOPPED,
    OMK_RECOVERY_MQTT_ONLY,
    /* budget_v1 persists these values. Retired reasons 7/8 must not be reused. */
    OMK_RECOVERY_ROOT_LINK = 9,
    OMK_RECOVERY_IP_TIMEOUT = 10,
} omk_mesh_recovery_reason_t;

typedef struct {
    bool started, is_root, parent_connected, rootless, has_ip;
    bool mqtt_started, mqtt_connected, mqtt_progress;
    bool control_stalled; /* A needed Mesh action has made no progress for 180 s. */
    uint32_t mqtt_disconnect_count;
} omk_mesh_recovery_input_t;

typedef struct {
    omk_mesh_recovery_stage_t stage;
    omk_mesh_recovery_reason_t reason;
    bool restart_spent, loss_active, explicit_accepted, attempted;
    bool healthy_active, mqtt_wait_active, ip_wait_active;
    bool role_known, last_is_root, last_started;
    uint32_t generation; /* RAM episode ID; changes on role/episode boundaries. */
    uint32_t ip_since_ms;
    bool retry_clock_started, retry_ready, persistence_blocked;
    uint32_t retry_since_ms;
    uint32_t loss_since_ms, explicit_since_ms, attempt_ms;
    uint32_t healthy_since_ms, mqtt_since_ms, mqtt_disconnect_count;
    uint32_t jitter_ms;
    uint32_t no_parent_found_count, stop_reconnection_count, parent_reselection_count;
    int last_scan_times;
} omk_mesh_recovery_t;

void mesh_recovery_init(omk_mesh_recovery_t *s, bool restart_spent, uint64_t node_id);
void mesh_recovery_note_no_parent(omk_mesh_recovery_t *s, int scan_times);
void mesh_recovery_note_stopped(omk_mesh_recovery_t *s);
/* Observe on connection/IP events as well as each status cycle. No actions here. */
void mesh_recovery_observe(omk_mesh_recovery_t *s, uint32_t now,
                           const omk_mesh_recovery_input_t *in);
/* Single arbiter. API failure must not fall through to a lower priority action. */
omk_mesh_recovery_action_t mesh_recovery_choose(omk_mesh_recovery_t *s, uint32_t now,
    const omk_mesh_recovery_input_t *in, omk_mesh_root_recovery_t *root);
void mesh_recovery_complete(omk_mesh_recovery_t *s, uint32_t now,
                            omk_mesh_recovery_action_t action, bool success);
uint32_t mesh_recovery_loss_duration_s(const omk_mesh_recovery_t *s, uint32_t now);
const char *mesh_recovery_stage_name(omk_mesh_recovery_stage_t stage);
const char *mesh_recovery_reason_name(omk_mesh_recovery_reason_t reason);

/* Re-evaluate a selected restart against live state without changing policy. */
bool mesh_recovery_restart_valid(const omk_mesh_recovery_t *selected,
    const omk_mesh_recovery_t *current, uint32_t now,
    const omk_mesh_recovery_input_t *in, const omk_mesh_root_recovery_t *root,
    bool selected_root);

/* Copied into the event payload at selection; never rebuilt at completion. */
typedef struct {
    omk_mesh_recovery_action_t action;
    uint32_t generation;
    bool is_root;
} omk_mesh_recovery_token_t;
omk_mesh_recovery_token_t mesh_recovery_action_token(const omk_mesh_recovery_t *s,
                                                     omk_mesh_recovery_action_t action);
/* Caller observes current role/state, checks and applies completion under the
 * same lock. No lock may be held across the actual Mesh API. */
bool mesh_recovery_action_matches(const omk_mesh_recovery_t *s,
                                  const omk_mesh_recovery_token_t *token);
