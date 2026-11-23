#pragma once

#include <stdbool.h>
#include <stdint.h>

/* SDK-independent role state. The transport event loop/lock owns mutation. */
typedef struct {
    bool started;
    bool parent_connected;
    bool is_root;
    bool has_ip;
    bool mqtt_connected;
    bool rootless;
    bool gateway_reachable;
    uint32_t epoch;
} mesh_session_t;

void mesh_session_invalidate(mesh_session_t *session);
bool mesh_session_set_role(mesh_session_t *session, bool root);
void mesh_session_started(mesh_session_t *session);
void mesh_session_stopped(mesh_session_t *session);
void mesh_session_parent_connected(mesh_session_t *session, bool root);
void mesh_session_parent_disconnected(mesh_session_t *session);
void mesh_session_set_rootless(mesh_session_t *session, bool rootless);
bool mesh_session_root_ready(const mesh_session_t *session);
bool mesh_session_got_ip(mesh_session_t *session, uint32_t observed_epoch);
bool mesh_session_mqtt_event(mesh_session_t *session, uint32_t callback_epoch, bool connected);
bool mesh_session_accept_ingress(const mesh_session_t *session);
bool mesh_session_accept_record(const mesh_session_t *session, uint32_t record_epoch,
                                uint64_t queued_ms, uint64_t now_ms);
