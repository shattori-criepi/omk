#include "mesh_session.h"
#include "mesh_wire.h"

void mesh_session_invalidate(mesh_session_t *s) {
    ++s->epoch;
    s->has_ip = false;
    s->mqtt_connected = false;
    s->gateway_reachable = false;
}

bool mesh_session_set_role(mesh_session_t *s, bool root) {
    if (s->is_root == root) return false;
    s->is_root = root;
    s->parent_connected = false;
    mesh_session_invalidate(s);
    return true;
}

void mesh_session_started(mesh_session_t *s) {
    s->started = true;
    s->rootless = true;
}

void mesh_session_stopped(mesh_session_t *s) {
    s->started = false;
    s->parent_connected = false;
    s->is_root = false;
    s->rootless = true;
    mesh_session_invalidate(s);
}

void mesh_session_parent_connected(mesh_session_t *s, bool root) {
    mesh_session_invalidate(s);
    s->is_root = root;
    s->parent_connected = true;
    s->rootless = false;
}

void mesh_session_parent_disconnected(mesh_session_t *s) {
    s->parent_connected = false;
    mesh_session_invalidate(s);
}

void mesh_session_set_rootless(mesh_session_t *s, bool rootless) {
    if (rootless && !s->rootless) {
        ++s->epoch;
        s->mqtt_connected = false;
        s->gateway_reachable = false;
        /* Rootless is a Mesh topology state, not an external lease loss.
         * Keep a live lease so ending a transient rootless state needs no
         * fabricated GOT_IP event or DHCP restart. */
    }
    s->rootless = rootless;
}

bool mesh_session_root_ready(const mesh_session_t *s) {
    return s->started && s->is_root && s->parent_connected && s->has_ip && !s->rootless;
}

bool mesh_session_got_ip(mesh_session_t *s, uint32_t observed_epoch) {
    if (s->epoch != observed_epoch || !s->started || !s->is_root ||
        !s->parent_connected || s->rootless) return false;
    s->has_ip = true;
    return true;
}

bool mesh_session_mqtt_event(mesh_session_t *s, uint32_t callback_epoch, bool connected) {
    if (s->epoch != callback_epoch || !mesh_session_root_ready(s)) return false;
    s->mqtt_connected = connected;
    return true;
}

bool mesh_session_accept_ingress(const mesh_session_t *s) {
    /* Draining Mesh and learning origins is independent of broker backpressure. */
    return s->started && s->is_root;
}

bool mesh_session_accept_record(const mesh_session_t *s, uint32_t record_epoch,
                                uint64_t queued_ms, uint64_t now_ms) {
    return mesh_session_accept_ingress(s) && s->epoch == record_epoch &&
           mesh_wire_is_fresh(queued_ms, now_ms);
}
