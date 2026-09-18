#pragma once

#include <stdbool.h>
#include <stdint.h>

/* This policy deliberately has no ESP-IDF dependency so it can be tested on
 * the host.  Time values are uint32_t milliseconds and comparisons tolerate
 * the normal unsigned timer rollover. */
#define OMK_MESH_ROOT_TOPOLOGY_STABILIZATION_MS 60000U
#define OMK_MESH_ROOT_REELECTION_COOLDOWN_MS 900000U
#define OMK_MESH_ROOT_REELECTION_GRACE_MS 120000U
#define OMK_MESH_ROOT_WEAK_RSSI_DBM (-80)
#define OMK_MESH_ROOT_PARENT_DISCONNECT_DELTA 3U
#define OMK_MESH_ROOT_MQTT_DISCONNECT_DELTA 2U

typedef enum {
    OMK_MESH_ROOT_REELECTION_NONE,
    OMK_MESH_ROOT_REELECTION_TOPOLOGY_CHANGE,
    OMK_MESH_ROOT_REELECTION_ROOT_LINK_UNHEALTHY,
} omk_mesh_root_reelection_reason_t;

typedef struct {
    bool pending;
    omk_mesh_root_reelection_reason_t reason;
    uint32_t due_ms;
    uint32_t cooldown_until_ms;
    uint32_t last_execution_ms;
    bool has_executed;
    uint32_t last_parent_disconnect_count;
    uint32_t last_mqtt_disconnect_count;
    bool counters_initialized;
} omk_mesh_root_recovery_t;

void mesh_root_recovery_init(omk_mesh_root_recovery_t *state);
void mesh_root_recovery_note_topology_change(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                              bool is_root);
void mesh_root_recovery_observe_link(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                     bool is_root, bool rssi_valid, int rssi_dbm,
                                     uint32_t parent_disconnect_count,
                                     uint32_t mqtt_disconnect_count);
bool mesh_root_recovery_should_execute(const omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                       bool is_root, bool parent_connected, bool rootless);
void mesh_root_recovery_mark_executed(omk_mesh_root_recovery_t *state, uint32_t now_ms);
bool mesh_root_recovery_in_grace(const omk_mesh_root_recovery_t *state, uint32_t now_ms);
const char *mesh_root_recovery_reason_name(omk_mesh_root_reelection_reason_t reason);
