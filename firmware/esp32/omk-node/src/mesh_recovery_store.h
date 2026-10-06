#pragma once

#include "esp_err.h"
#include "mesh_recovery.h"

typedef struct {
    uint32_t version, spent, restart_count, reason;
} omk_mesh_recovery_record_t;

/* A missing record is a fresh budget. Every other read error fails closed. */
esp_err_t mesh_recovery_store_load(omk_mesh_recovery_record_t *record);
/* Commit and reopen/read-back the single record before allowing a restart. */
esp_err_t mesh_recovery_store_save(omk_mesh_recovery_record_t *record, bool spent,
                                  omk_mesh_recovery_reason_t reason);
