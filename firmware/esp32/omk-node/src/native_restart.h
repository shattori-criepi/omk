#pragma once
#include <stdbool.h>
#include <stdint.h>

#define NATIVE_RESTART_HOLDOFF_MS UINT64_C(3600000)
/* Initialize the relay inventory match and retained budget before transport starts. */
void native_restart_init(const char *node_id);
bool native_restart_allowed(uint64_t uptime_ms);
bool native_restart_previous_stall(void);
/* Plain RTC writes only; caller commits under its final send-state guard. */
void native_restart_record(uint32_t call_id, uint32_t age_ms, bool reselected);
