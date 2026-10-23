#include "mesh_liveness.h"

#include <stddef.h>

bool mqtt_progress_probe_due(const omk_mqtt_progress_t *s, uint32_t now, bool connected) {
    return connected && !s->submitting && s->pending_id == 0 &&
           (!s->attempted || (uint32_t)(now - s->attempt_ms) >= OMK_MQTT_PROBE_INTERVAL_MS);
}

bool mqtt_progress_begin(omk_mqtt_progress_t *s, uint32_t now, bool connected) {
    if (!mqtt_progress_probe_due(s, now, connected)) return false;
    s->submitting = true;
    return true;
}

void mqtt_progress_result(omk_mqtt_progress_t *s, uint32_t now, int message_id) {
    s->attempted = true;
    s->attempt_ms = now;
    s->pending_id = message_id > 0 ? message_id : 0;
    s->submitting = false;
}

void mqtt_progress_event(omk_mqtt_progress_t *s, uint32_t now, int message_id, bool acknowledged) {
    if (s->submitting) return; /* Only unrelated IDs can precede installation. */
    if (message_id <= 0 || message_id != s->pending_id) return;
    s->pending_id = 0;
    if (acknowledged) {
        s->ack_seen = true;
        s->ack_ms = now;
        ++s->ack_count;
    }
}

bool mqtt_progress_is_fresh(const omk_mqtt_progress_t *s, uint32_t now) {
    return s->ack_seen && (uint32_t)(now - s->ack_ms) < OMK_MQTT_PROGRESS_FRESH_MS;
}
