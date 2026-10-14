#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "communication_limits.h"

/* Caller serializes these short RAM operations. No network calls or sleeps. */
typedef struct {
    uint32_t interval_ms, failure_ms, admitted_ms;
    unsigned successes;
    bool admitted;
} omk_mesh_tx_policy_t;
void mesh_tx_policy_result(omk_mesh_tx_policy_t *s, uint32_t now, bool pressure, bool success);
bool mesh_tx_policy_allow(omk_mesh_tx_policy_t *s, uint32_t now);
