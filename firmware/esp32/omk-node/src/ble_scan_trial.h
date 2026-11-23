#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define AB_PRESETS 4U
#define AB_UNKNOWN 4U
#define AB_MAX_SEGMENTS 4U
#define AB_ACCEPT_MS UINT64_C(120000)
#define AB_PLAN_MS UINT64_C(36000000)
#define AB_STABLE_MS UINT64_C(5000)
#define AB_GAP_MS UINT64_C(2000)
#define AB_RECORD_SIZE 256U
#define AB_JSON_MAX 480U

enum ab_plan_state { AB_PLAN_NONE, AB_PLAN_ACTIVE, AB_PLAN_SUSPENDED, AB_PLAN_FINISHED };
enum ab_op { AB_OP_NONE, AB_OP_STOP, AB_OP_PARAM, AB_OP_START };
enum ab_result { AB_RESULT_NONE, AB_ACCEPTED, AB_DUPLICATE, AB_APPLIED, AB_EXPIRED,
    AB_CANCELLED, AB_ROOT_CHANGED, AB_BAD_COMMAND, AB_OLD_EPOCH, AB_CONFLICT,
    AB_BUSY, AB_WRONG_BOOT, AB_BAD_ROOT, AB_BAD_TIME, AB_GAP_FAILURE, AB_GAP_TIMEOUT };
typedef struct { uint64_t start_ms, end_ms; uint8_t preset; } ab_segment_t;
typedef struct {
    uint64_t accept_before_ms, end_ms, nonce;
    uint32_t epoch;
    uint8_t count;
    bool cancel;
    ab_segment_t segments[AB_MAX_SEGMENTS];
} ab_plan_t;
typedef struct {
    bool started, parent, root, rootless, voting;
    uint8_t root_mac[6];
    uint32_t link_epoch, root_changes, observation;
    uint64_t root_nonce;
} ab_topology_t;
typedef struct {
    ab_plan_t plan;
    ab_topology_t topology;
    uint8_t bound_root[6];
    uint32_t bound_changes, max_epoch, change;
    uint64_t stable_since, op_since, applied_ms;
    uint8_t plan_state, desired, configured, operation, op_preset, result, segment;
    bool ready, running, known, failed, restoring, timed_out, stable;
} ab_scan_t;
uint16_t ab_window(uint8_t preset);
bool ab_mac_valid(const uint8_t mac[6]);
void ab_scan_init(ab_scan_t *s);
void ab_scan_topology(ab_scan_t *s, const ab_topology_t *topology, uint64_t now);
void ab_scan_role(ab_scan_t *s, bool root);
enum ab_result ab_scan_accept(ab_scan_t *s, const ab_plan_t *plan, uint64_t now);
enum ab_op ab_scan_tick(ab_scan_t *s, uint64_t now);
void ab_scan_complete(ab_scan_t *s, enum ab_op op, bool success, uint64_t now);
void ab_scan_ready(ab_scan_t *s);
uint8_t ab_scan_effective(const ab_scan_t *s);

/* Parser is pure and bounded. Identities remain transport/application checks. */
typedef struct {
    ab_plan_t plan;
    char node_id[13], boot_id[17], root_id[13];
} ab_command_t;
bool ab_command_parse(const char *payload, size_t length, ab_command_t *out);
