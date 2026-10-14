#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Keep this policy independent of ESP-IDF so its boundary conditions can be
 * verified on the host. */
#define OMK_MESH_MQTT_LIVENESS_TIMEOUT_S 180U
#define OMK_MQTT_PROBE_INTERVAL_MS 30000U
#define OMK_MQTT_PROGRESS_FRESH_MS 90000U

/* Caller serializes RAM operations. Install the returned ID while still
 * holding MQTT's API mutex, before its task can dispatch a terminal event.
 * Outstanding IDs survive reconnects just like the MQTT outbox. */
typedef struct {
    int pending_id;
    bool attempted, ack_seen, submitting;
    uint32_t attempt_ms, ack_ms, ack_count;
} omk_mqtt_progress_t;

bool mqtt_progress_probe_due(const omk_mqtt_progress_t *s, uint32_t now, bool connected);
bool mqtt_progress_begin(omk_mqtt_progress_t *s, uint32_t now, bool connected);
void mqtt_progress_result(omk_mqtt_progress_t *s, uint32_t now, int message_id);
void mqtt_progress_event(omk_mqtt_progress_t *s, uint32_t now, int message_id, bool acknowledged);
bool mqtt_progress_is_fresh(const omk_mqtt_progress_t *s, uint32_t now);

typedef struct {
    bool mesh_started;
    bool parent_connected;
    bool rootless;
    bool has_ip;
    bool normal_operation;
    bool mqtt_connected;
    bool mqtt_progress;
    uint32_t mqtt_disconnected_duration_s;
} omk_mesh_mqtt_liveness_state_t;

bool mesh_mqtt_liveness_should_recover(const omk_mesh_mqtt_liveness_state_t *state);
