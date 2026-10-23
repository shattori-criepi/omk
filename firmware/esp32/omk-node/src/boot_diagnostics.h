#pragma once

#include <stdint.h>

/* Captures immutable per-boot diagnostics. NVS failure never blocks startup. */
void boot_diagnostics_init(void);
const char *boot_diagnostics_reset_reason(void);
uint32_t boot_diagnostics_reset_reason_code(void);
uint32_t boot_diagnostics_boot_count(void);
/* Historical values written by older firmware; not the current reset cause. */
const char *boot_diagnostics_last_omk_restart_reason(void);
uint32_t boot_diagnostics_mesh_mqtt_liveness_restart_count(void);
