#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* This measures application esp_mesh_send calls, NOT libmesh forwarding. */
enum { ND_MESH_UP, ND_MESH_DOWN, ND_MESH_BROADCAST, ND_ENQUEUE,
       ND_OUTBOX, ND_WRITE, ND_CONTROL, ND_RECOVERY, ND_OPERATIONS };
enum { ND_ENTER = 1, ND_WAIT_LOCK, ND_LOCKED };
#define ND_ACTIVE_SLOTS 8
#define ND_SCHEMA 2U
#define ND_REPORT_SIZE 3072

typedef struct {
    uint32_t calls, completed, errors;
    int32_t last_rc;
    uint32_t last_ms, max_ms, last_wait_ms, max_wait_ms, last_locked_ms;
    uint32_t over_100ms, over_1s, over_10s;
    uint64_t last_end_us;
} nd_stats_t;
typedef struct {
    uint32_t token, task, op, phase;
    uint64_t started_us, wait_us, acquired_us;
} nd_active_t;
typedef struct {
    uint32_t ticks, posted, post_errors, entered, exited;
    uint32_t recovery_flags, recovery_stage, recovery_action;
    uint64_t tick_us, post_us, enter_us, exit_us, recovery_us;
} nd_progress_t;
typedef struct { int32_t rc; uint32_t count; } nd_mesh_error_t;
typedef struct {
    uint32_t schema, sequence, reasons, saved_reasons, saves;
    int32_t save_error;
    uint32_t dropped, usb_dropped;
    uint64_t boot_id, sampled_us;
    uint64_t flash_started_us, flash_finished_us;
    uint8_t elf_sha[32];
    nd_progress_t progress;
    nd_stats_t stats[ND_OPERATIONS];
    nd_active_t active[ND_ACTIVE_SLOTS];
    nd_mesh_error_t mesh_errors[8];
    uint32_t mesh_error_other;
    uint32_t critical_reasons, saved_critical;
} nd_snapshot_t;
_Static_assert(sizeof(nd_snapshot_t) <= 1024, "Bound diagnostic copy and NVS size");

#ifdef OMK_NETWORK_DIAGNOSTICS
uint32_t network_diag_begin(unsigned op);
void network_diag_wait(uint32_t token);
void network_diag_locked(uint32_t token);
int network_diag_end(uint32_t token, int rc, bool failed);
void network_diag_tick(uint64_t at_us);
void network_diag_post(uint64_t at_us, int rc);
void network_diag_control(bool entering);
void network_diag_recovery(uint32_t flags, uint32_t stage, uint32_t action);
void network_diag_snapshot(nd_snapshot_t *out);
uint32_t network_diag_reasons(const nd_snapshot_t *s);
uint32_t network_diag_critical(const nd_snapshot_t *s);
void network_diag_init_core(uint64_t boot_id, const uint8_t elf_sha[32]);
void network_diag_start(void);
bool network_diag_report(char *out, size_t size);
void network_diag_report_result(bool enqueued);
/* ASCII request is parsed before cJSON: no allocation, network call or reset. */
bool network_diag_usb(const char *line, uint64_t node_id);
#else
static inline uint32_t network_diag_begin(unsigned op) { (void)op; return 0; }
static inline void network_diag_wait(uint32_t token) { (void)token; }
static inline void network_diag_locked(uint32_t token) { (void)token; }
static inline int network_diag_end(uint32_t t, int rc, bool failed) { (void)t; (void)failed; return rc; }
static inline void network_diag_tick(uint64_t t) { (void)t; }
static inline void network_diag_post(uint64_t t, int rc) { (void)t; (void)rc; }
static inline void network_diag_control(bool entering) { (void)entering; }
static inline void network_diag_recovery(uint32_t f, uint32_t s, uint32_t a) { (void)f; (void)s; (void)a; }
static inline void network_diag_start(void) {}
static inline bool network_diag_report(char *p, size_t n) { (void)p; (void)n; return false; }
static inline bool network_diag_usb(const char *p, uint64_t id) { (void)p; (void)id; return false; }
#endif
