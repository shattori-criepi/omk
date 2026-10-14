#include "mesh_tx_policy.h"

void mesh_tx_policy_result(omk_mesh_tx_policy_t *s, uint32_t now, bool pressure, bool success) {
    if (pressure) {
        s->interval_ms = !s->interval_ms ? OMK_MESH_BACKOFF_MIN_MS :
            s->interval_ms > OMK_MESH_BACKOFF_MAX_MS / 2 ? OMK_MESH_BACKOFF_MAX_MS : s->interval_ms * 2;
        if (s->interval_ms > OMK_MESH_BACKOFF_MAX_MS) s->interval_ms = OMK_MESH_BACKOFF_MAX_MS;
        s->failure_ms = now;
        s->successes = 0;
    } else if (success && s->interval_ms) {
        if (s->successes < 3) ++s->successes;
        /* One intermittent success must not reopen a burst of normal data. */
        if (s->successes >= 3 && (uint32_t)(now - s->failure_ms) >= OMK_MESH_BACKOFF_RECOVER_MS) {
            s->interval_ms = 0;
            s->admitted = false;
        }
    } else if (!success) s->successes = 0;
}

bool mesh_tx_policy_allow(omk_mesh_tx_policy_t *s, uint32_t now) {
    if (!s->interval_ms) return true;
    if ((uint32_t)(now - s->failure_ms) < s->interval_ms ||
        (s->admitted && (uint32_t)(now - s->admitted_ms) < s->interval_ms)) return false;
    s->admitted = true;
    s->admitted_ms = now;
    return true;
}
