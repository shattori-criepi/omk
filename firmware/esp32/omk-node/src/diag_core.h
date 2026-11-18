#pragma once
/* Pure bounded diagnostic state. Caller serializes access; no SDK/heap/I/O. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "mesh_probe.h"

#define DIAG_SCHEMA 3U
/* Trade redundant transition history for the complete durable Wi-Fi probe. */
#define DIAG_EVENT_COUNT 5U
#define DIAG_PEER_COUNT 4U
#define DIAG_ERROR_COUNT 8U /* OK, six selected Mesh errors, other */
#define DIAG_STAGE_COUNT 8U
#define DIAG_STALL_MS UINT64_C(180000)
#define DIAG_EARLY_STALL_MS UINT64_C(30000)
#define DIAG_RECENT_MS UINT64_C(60000)
#define DIAG_F_STARTED 1U
#define DIAG_F_PARENT 2U
#define DIAG_F_ROOT 4U
#define DIAG_F_IP 8U
#define DIAG_F_MQTT 16U
#define DIAG_F_ROOTLESS 32U
#define DIAG_REC_TX 1U
#define DIAG_REC_DELIVERY 2U
#define DIAG_REC_REPEATED 4U

enum { DIAG_E_TX = 1, DIAG_E_MESH, DIAG_E_REC_REQUEST, DIAG_E_REC_RESULT,
       DIAG_E_RECEIPT, DIAG_E_IO_BEGIN, DIAG_E_IO_END, DIAG_E_BLE_SCAN };
enum { DIAG_CAPTURE_NONE, DIAG_CAPTURE_TX_STALL, DIAG_CAPTURE_RECOVERY,
       DIAG_CAPTURE_PEER_SILENT, DIAG_CAPTURE_FOLLOWUP, DIAG_CAPTURE_DELIVERY_STALL, DIAG_CAPTURE_KIND_STALL, DIAG_CAPTURE_ROOT_FAILURE,
       DIAG_CAPTURE_ONSET, DIAG_CAPTURE_PARENT_STALL, DIAG_CAPTURE_QUALITY, DIAG_CAPTURE_RECEIPT_SILENT };
enum { DIAG_ROOT_WIRE_BAD, DIAG_ROOT_RX, DIAG_ROOT_INGRESS_REJECT,
       DIAG_ROOT_BRIDGE_REJECT, DIAG_ROOT_MQTT_OFFLINE, DIAG_ROOT_MQTT_FULL,
       DIAG_ROOT_MQTT_ERROR, DIAG_ROOT_MQTT_ACCEPT };

typedef struct __attribute__((packed)) {
    uint8_t kind, valid;
    uint16_t reserved;
    char node_id[13], boot_id[17];
    uint16_t padding;
    uint32_t seq;
} diag_identity_t;

typedef struct __attribute__((packed)) {
    uint32_t flags;
    int32_t layer;
    uint8_t parent[6], root[6];
} diag_topology_t;

typedef struct __attribute__((packed)) {
    diag_topology_t topology;
    uint32_t tx_accepted, tx_dropped, rx_dropped, mesh_rx_ok, mesh_rx_errors;
    uint32_t mqtt_published, parent_disconnects, root_changes;
    uint32_t recovery_requests, recovery_ok, recovery_link, mqtt_pubacks;
} diag_context_t;

typedef struct __attribute__((packed)) {
    uint64_t ms;
    uint32_t type;
    int32_t code;
    uint32_t detail, duration_us, tx_ok, tx_errors;
    diag_topology_t topology;
    diag_identity_t identity;
} diag_event_t;

typedef struct __attribute__((packed)) {
    uint64_t ms;
    uint32_t valid;
    int32_t tx_pending[6], rx_pending[2], tx_result, rx_result;
    uint32_t heap_free, heap_min, heap_largest;
    uint32_t counts[DIAG_ERROR_COUNT];
} diag_sample_t;

typedef struct __attribute__((packed)) {
    diag_identity_t rx_identity;
    uint64_t rx_ms;
    diag_identity_t stage_identity;
    uint64_t stage_ms;
    uint32_t stage, rx_count, mqtt_accepted;
    uint64_t failure_since_ms, last_failure_ms;
} diag_peer_t;

typedef struct __attribute__((packed)) {
    uint32_t schema, bytes;
    char firmware[24], node_id[13], boot_id[17];
    uint16_t reserved;
    uint32_t reset_reason;
    uint64_t capture_ms;
    uint32_t capture_reason;
    diag_context_t context;
    uint32_t counts[DIAG_ERROR_COUNT];
    uint64_t first_seen_ms[DIAG_ERROR_COUNT];
    int32_t last_result;
    uint32_t last_duration_us, max_duration_us;
    uint64_t tx_start_us;
    uint32_t inflight, upstream;
    diag_identity_t active_identity, last_ok_identity;
    uint64_t last_ok_ms, first_upstream_ms, last_upstream_ms, last_upstream_ok_ms;
    uint32_t upstream_attempts;
    uint32_t first_error_valid, onset_sample_needed;
    diag_event_t first_error;
    diag_sample_t baseline, onset, current;
    diag_event_t episode_first_error;
    diag_sample_t episode_baseline, episode_onset;
    uint64_t last_error_ms;
    uint32_t episode_number;
    uint64_t kind_last_ok_ms[7], kind_first_error_ms[7], kind_last_error_ms[7];
    uint32_t kind_error_mask;
    diag_event_t events[DIAG_EVENT_COUNT];
    uint32_t event_head, event_count, event_overwrites;
    uint32_t root_counts[DIAG_STAGE_COUNT];
    diag_peer_t peers[DIAG_PEER_COUNT];
    uint32_t peer_overflows;
    diag_identity_t last_root_failure;
    uint64_t last_root_failure_ms;
    uint32_t last_root_failure_stage;
    diag_identity_t last_receipt;
    uint64_t last_receipt_ms;
    uint32_t receipt_count, recovery_trigger;
    uint64_t demand_observed_ms, tx_demand_since_ms, delivery_demand_since_ms;
    uint64_t delivery_proven_ms, inhibit_until_ms, root_since_ms;
    uint32_t tx_demand, delivery_demand, voting;
    uint64_t observation_start_ms;
    /* Schema 2 appends fields: schema 1 records remain independently readable. */
    uint32_t arm_generation, inflight_before_arm;
    uint32_t request_count[7], sdk_count[7], sdk_errors[7];
    /* 0 unknown/encrypted, 1 Motion, 2 Contact, 3 Presence. */
    uint32_t ble_periodic[4], ble_event[4], ble_rejected[4], ble_suppressed[4];
    /* Schema 2 length extension (3604 B; original LR2 was 3592 B).
     * First failed save in this observation window; 0 means no failed slot,
     * otherwise slot+1. A later committed snapshot preserves this across boot. */
    uint32_t save_failure_slot_plus1, save_failure_phase;
    int32_t save_failure_error;
    /* Schema 3. Fixed disconnect evidence survives event-ring overwrite. */
    uint64_t link_bad_since_ms, disconnect_ms;
    uint8_t disconnected_parent[6], disconnect_reason;
    int8_t disconnect_rssi;
    uint32_t capture_sequence, quality_bad;
    mesh_probe_core_t probe;
    uint64_t role_changed_ms; /* schema 3 length extension: 4056 B */
} diag_core_t;

_Static_assert(sizeof(diag_identity_t) == 40, "diagnostic identity layout");
_Static_assert(sizeof(diag_event_t) == 92, "diagnostic event layout");
_Static_assert(sizeof(diag_core_t) <= 4056, "diagnostic payload budget");
_Static_assert(offsetof(diag_core_t, probe) == 2624, "schema 3 probe offset");

void diag_core_init(diag_core_t *s, const char *node_id, const char *boot_id,
                    const char *firmware, uint32_t reset_reason);
/* Bounded top-level identity extractor, not a replacement for JSON validation.
 * Missing, malformed or ambiguous identity fields => valid=0. */
void diag_identity_read(diag_identity_t *id, uint8_t kind, const char *node_id,
                        const char *payload, size_t length);
void diag_core_context(diag_core_t *s, const diag_context_t *context, uint64_t now_ms);
void diag_core_demand(diag_core_t *s, uint64_t now_ms, bool tx_demand,
                      bool delivery_demand, uint64_t delivery_proven_ms,
                      uint64_t inhibit_until_ms, bool voting);
unsigned diag_error_bucket(int32_t error);
void diag_core_tx_begin(diag_core_t *s, uint64_t now_us,
                        const diag_identity_t *id, bool upstream);
void diag_core_tx_end(diag_core_t *s, uint64_t now_us, int32_t error);
void diag_core_event(diag_core_t *s, uint64_t now_ms, uint32_t type,
                     int32_t code, uint32_t detail);
void diag_core_sample(diag_core_t *s, const diag_sample_t *sample);
void diag_core_receipt(diag_core_t *s, uint64_t now_ms, const diag_identity_t *id);
void diag_core_recovery(diag_core_t *s, uint64_t now_ms, uint32_t action,
                        uint32_t reason_flags, bool finished, int32_t result);
/* RX is remote SEN66/status only. Other stages count all remote records. */
void diag_core_root(diag_core_t *s, uint64_t now_ms,
                    const diag_identity_t *id, unsigned stage);
uint32_t diag_core_trigger(const diag_core_t *s, uint64_t now_ms);
void diag_core_capture(diag_core_t *s, uint64_t now_ms, uint32_t reason);
