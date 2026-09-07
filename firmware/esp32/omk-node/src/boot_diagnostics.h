#pragma once

#include <stdint.h>

/* Captures immutable per-boot diagnostics. NVS failure never blocks startup. */
void boot_diagnostics_init(void);
const char *boot_diagnostics_reset_reason(void);
uint32_t boot_diagnostics_reset_reason_code(void);
uint32_t boot_diagnostics_boot_count(void);
const char *boot_diagnostics_last_omk_restart_reason(void);
uint32_t boot_diagnostics_mesh_mqtt_liveness_restart_count(void);
/* Best-effort persistence before a deliberate OMK software restart. */
void boot_diagnostics_record_restart_reason(const char *reason);
