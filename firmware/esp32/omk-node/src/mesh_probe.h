#pragma once
#include <stdint.h>
#include <stddef.h>
#include "mesh_quality.h"
enum { PROBE_VALID=1, PROBE_CHANGING=2, PROBE_MIXED=4 };
enum { PROBE_BEGIN=1, PROBE_API, PROBE_DISCONNECTED, PROBE_LINKED,
       PROBE_RECOVERED, PROBE_UNRECOVERED, PROBE_DEFERRED, PROBE_CANCELLED, PROBE_RESTORE_FAILED };
typedef struct {
    uint64_t us;
    uint32_t alloc_raw, recycled;
    uint16_t used, limit, malloc_failed, rejected;
    uint32_t flags, reserved;
} mesh_probe_raw_t;
typedef struct {
    mesh_probe_raw_t raw;
    uint32_t call_id, epoch, scan_change, duration_us;
    int32_t result;
    uint32_t meta; /* kind[7:0], effective preset[15:8], root[16], phase[31:24] */
    uint8_t parent[6], root[6];
    uint32_t reserved[3];
} mesh_probe_event_t;
/* USB format 1; also embedded verbatim in Flash schema 3. Kind 8 is unchanged. */
typedef struct {
    uint32_t version, bytes, abi_flags, ring_overwrites;
    char node[13], boot[17];
    uint16_t reserved;
    uint32_t call_sequence, ring_head, ring_count, seen;
    uint64_t recovery_attempt, phase_ms;
    uint32_t quality[10], recovery_phase, subtree_nodes;
    mesh_probe_event_t events[13];
    mesh_probe_raw_t recent[8];
} mesh_probe_core_t;
_Static_assert(sizeof(mesh_probe_raw_t)==32,"private TX snapshot");
_Static_assert(sizeof(mesh_probe_event_t)==80,"private TX event");
_Static_assert(sizeof(mesh_probe_core_t)==1424,"bounded V2 USB record");
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE && OMK_DIAGNOSTICS
void mesh_probe_boot(const char *node,const char *boot);
void mesh_probe_begin(uint8_t kind,uint32_t epoch);
void mesh_probe_end(int32_t result,uint32_t epoch);
void mesh_probe_poll(void);
void mesh_probe_recovery(unsigned phase,int32_t result,unsigned subtree_nodes);
void mesh_probe_quality(const mesh_quality_view_t *view);
void mesh_probe_copy(void *out);
#else
#define mesh_probe_boot(...) ((void)0)
#define mesh_probe_begin(...) ((void)0)
#define mesh_probe_end(...) ((void)0)
#define mesh_probe_poll(...) ((void)0)
#define mesh_probe_recovery(...) ((void)0)
#define mesh_probe_quality(...) ((void)0)
#define mesh_probe_copy(out) memset((out),0,sizeof(mesh_probe_core_t))
#endif
