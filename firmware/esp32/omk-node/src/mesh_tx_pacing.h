#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Single sender owns this state. No retry, pending getter, new queue or radio
 * API. Three consecutive SDK TIMEOUT/NO_MEMORY results start a five-second
 * inter-call respite. Link/config/recovery changes do not prove SDK resource
 * release. Only an upstream SDK success clears it; Root delivery is still
 * judged independently by STATUS_RECEIPT / DELIVERY_STALL. */
typedef struct {
    uint64_t returned_ms;
    unsigned resource_errors;
    bool active;
} mesh_tx_pacing_t;

static inline bool mesh_tx_pacing_wait(const mesh_tx_pacing_t *p, uint64_t now,
        bool child) {
    /* Bypass while Root/disconnected, retaining resource history for rejoin.
     * This never extends the deadline: the next ordinary record is the probe. */
    return child && p->active && now - p->returned_ms < UINT64_C(5000);
}
static inline void mesh_tx_pacing_result(mesh_tx_pacing_t *p, uint64_t now, int32_t error) {
    if (!error) { p->resource_errors = 0; p->active = false; }
    else if (error == 0x400a || error == 0x4007) { /* IDF 6.0.1 TIMEOUT / NO_MEMORY */
        if (p->resource_errors < 3) ++p->resource_errors;
        if (p->resource_errors == 3) p->active = true;
    } else if (!p->active) p->resource_errors = 0;
    p->returned_ms = now;
}
