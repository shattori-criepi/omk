#pragma once
#include "ble_scan_metrics.h"
/* Single bounded state block. No GAP, Mesh, Flash or NVS I/O under its lock. */
void ab_runtime_boot(const char *node, const char *boot);
void ab_runtime_topology(const ab_topology_t *topology);
void ab_runtime_role(bool root);
void ab_runtime_ready(void);
enum ab_op ab_runtime_tick(uint16_t *window);
void ab_runtime_complete(enum ab_op op, bool success);
bool ab_runtime_receiving(void);
enum ab_result ab_runtime_command(const char *payload,size_t length,const uint8_t source[6]);
void ab_runtime_demand(void);
void ab_runtime_sdk_begin(uint8_t kind,bool upstream);
void ab_runtime_sdk_end(int32_t error);
void ab_runtime_drop(void);
void ab_runtime_pack(uint8_t out[AB_RECORD_SIZE]);
void ab_runtime_probe_context(uint8_t *effective,uint32_t *change);
void ab_runtime_diagnostic(uint32_t heap_free,uint32_t heap_min,uint32_t arm,
                           uint8_t masks,uint8_t phase,uint8_t error);

bool ab_runtime_report_due(void);
void ab_runtime_reported(void);
bool ab_runtime_periodic_allowed(void);

typedef struct { uint64_t ms; uint32_t epoch, detail; } ab_event_t;
bool ab_runtime_event(ab_event_t *event);
