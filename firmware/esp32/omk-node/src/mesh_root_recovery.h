#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Host-testable policy. Timers use uint32_t elapsed time and latch expiry.
 * The 30-second control cycle observes active timers before a full ms wrap. */
#define OMK_MESH_ROOT_REELECTION_COOLDOWN_MS 900000U
#define OMK_MESH_ROOT_REELECTION_GRACE_MS 120000U
#define OMK_MESH_ROOT_WEAK_RSSI_DBM (-80)
#define OMK_MESH_ROOT_PARENT_DISCONNECT_DELTA 3U
#define OMK_MESH_ROOT_MQTT_DISCONNECT_DELTA 2U

typedef struct {
    bool pending;
    uint32_t last_execution_ms;
    bool cooldown_active;
    bool grace_active;
    uint32_t last_parent_disconnect_count;
    uint32_t last_mqtt_disconnect_count;
    bool counters_initialized;
} omk_mesh_root_recovery_t;

void mesh_root_recovery_init(omk_mesh_root_recovery_t *state);
void mesh_root_recovery_observe_link(omk_mesh_root_recovery_t *state, uint32_t now_ms,
                                     bool is_root, bool rssi_valid, int rssi_dbm,
                                     uint32_t parent_disconnect_count,
                                     uint32_t mqtt_disconnect_count);
/* Only root_link_unhealthy can request a vote. Queries also expire timers. */
bool mesh_root_recovery_should_execute(
    omk_mesh_root_recovery_t *state, uint32_t now_ms,
    bool is_root, bool parent_connected, bool rootless);
/* Record only a successful API request, not completion of Mesh reconfiguration. */
void mesh_root_recovery_mark_executed(omk_mesh_root_recovery_t *state, uint32_t now_ms);
bool mesh_root_recovery_in_grace(omk_mesh_root_recovery_t *state, uint32_t now_ms);
