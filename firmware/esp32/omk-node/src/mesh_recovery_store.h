#pragma once

#include "esp_err.h"
#include "mesh_recovery.h"

typedef struct {
    uint32_t version, spent, restart_count, reason;
} omk_mesh_recovery_record_t;

/* Read-only compatibility history. Never creates/erases keys or reserves a
 * restart. Missing/invalid records cannot influence live recovery policy. */
esp_err_t mesh_recovery_store_load(omk_mesh_recovery_record_t *record);
