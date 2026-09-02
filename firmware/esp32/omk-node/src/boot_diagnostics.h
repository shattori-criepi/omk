#pragma once

#include <stdint.h>

/* Captures immutable per-boot diagnostics. NVS failure never blocks startup. */
void boot_diagnostics_init(void);
const char *boot_diagnostics_reset_reason(void);
uint32_t boot_diagnostics_reset_reason_code(void);
uint32_t boot_diagnostics_boot_count(void);
