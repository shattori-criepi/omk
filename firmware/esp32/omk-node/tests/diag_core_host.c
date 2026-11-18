#include "diag_core.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static diag_core_t state;
static const char *node = "aabbccddeeff", *boot = "0123456789abcdef";
static diag_identity_t identity(unsigned kind, unsigned seq) {
    char json[120]; diag_identity_t id;
    snprintf(json, sizeof(json), "{\"boot_id\":\"%s\",\"seq\":%u}", boot, seq);
    diag_identity_read(&id, (uint8_t)kind, node, json, strlen(json));
    assert(id.valid && id.seq == seq && id.kind == kind);
    return id;
}
static void init(bool root) {
    diag_core_init(&state, node, boot, "AB5-DIAG1", 3);
    diag_context_t c = {0};
    c.topology.flags = DIAG_F_STARTED | DIAG_F_PARENT | (root ? DIAG_F_ROOT : 0);
    c.topology.layer = root ? 1 : 3;
    memcpy(c.topology.parent, "PARENT", 6); memcpy(c.topology.root, "ROOT00", 6);
    diag_core_context(&state, &c, 0);
}
static void send(unsigned kind, unsigned seq, uint64_t ms, int32_t error, uint32_t us) {
    diag_identity_t id = identity(kind, seq);
    diag_core_tx_begin(&state, ms * 1000, &id, true);
    diag_core_tx_end(&state, ms * 1000 + us, error);
}
static void sample(uint64_t ms, unsigned heap) {
    diag_sample_t v = {0}; v.ms = ms; v.heap_free = heap;
    v.heap_min = heap - 100; v.heap_largest = heap / 2;
    v.tx_pending[0] = 2; v.rx_pending[1] = 1;
    diag_core_sample(&state, &v);
}
static void normal(void) {
    init(false); sample(100, 100000);
    for (unsigned i = 1; i <= 100; ++i) send(1, i, i * 10000, 0, 234);
    assert(state.counts[0] == 100 && state.event_count == 0 && !state.first_error_valid);
    assert(state.last_ok_identity.seq == 100 && state.max_duration_us == 234);
    diag_identity_t receipt = identity(3, 1); diag_core_receipt(&state, 1000000, &receipt);
    assert(diag_core_trigger(&state, 1000001) == DIAG_CAPTURE_NONE);
    assert(state.current.valid && state.baseline.heap_free == 100000);
}
static void first_and_transitions(void) {
    init(false); sample(100, 100000); send(1, 1, 1000, 0, 50);
    send(1, 2, 2000, 0x400a, 5000000);
    assert(state.first_error_valid && state.first_error.code == 0x400a && state.first_error.tx_ok == 1);
    assert(state.first_error.ms == 7000 && state.first_error.identity.seq == 2 && state.first_error.duration_us == 5000000);
    assert(state.first_error.topology.layer == 3 && state.first_error.tx_errors == 1);
    assert(state.baseline.ms == 100 && state.baseline.heap_free == 100000);
    assert(state.onset_sample_needed == 3 && state.episode_number == 1);
    sample(7100, 90000);
    assert(!state.onset_sample_needed && state.onset.ms == 7100 && state.episode_onset.ms == 7100);
    send(1, 3, 8000, 0x400a, 50); send(1, 4, 9000, 0x4007, 10); send(1, 5, 10000, 0, 20);
    assert(state.event_count == 3 && state.events[0].code == 0x400a && state.events[1].code == 0x4007 && !state.events[2].code);
    assert(state.counts[0] == 2 && state.counts[1] == 2 && state.counts[2] == 1);
    assert(state.first_error.identity.seq == 2 && state.last_ok_identity.seq == 5);
    assert(state.first_seen_ms[0] == 1000 && state.first_seen_ms[1] == 7000 && state.first_seen_ms[2] == 9000);
    assert(diag_core_trigger(&state, 200000) == DIAG_CAPTURE_NONE);
}
static void sustained(void) {
    init(false); send(1, 1, 1000, 0, 10);
    for (unsigned i = 1; i <= 19; ++i) send(1, i + 1, 1000 + i * 10000, 0x400a, 10);
    assert(diag_core_trigger(&state, 191001) == DIAG_CAPTURE_KIND_STALL);
    assert(diag_core_trigger(&state, 300000) == DIAG_CAPTURE_RECEIPT_SILENT); /* independent of demand */
    send(1, 22, 300001, 0, 1);
    assert(diag_core_trigger(&state, 300002) == DIAG_CAPTURE_RECEIPT_SILENT); /* SDK OK is not receipt */
}
static void inflight(void) {
    init(false); diag_identity_t id = identity(1, 1);
    diag_core_tx_begin(&state, 1000000, &id, true);
    assert(diag_core_trigger(&state, 30999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 31000) == DIAG_CAPTURE_TX_STALL);
    assert(state.inflight && state.active_identity.seq == 1 && !state.counts[0]);
    diag_core_tx_end(&state, UINT64_C(5000000000), 0x400a);
    assert(state.max_duration_us == UINT32_MAX && !state.inflight);
}
static void occasional_success(void) {
    init(false);
    for (unsigned i = 0; i < 100; ++i) {
        send(1, i, 1000 + i * 10000, i % 10 == 0 ? 0 : 0x400a, 1);
        diag_identity_t receipt = identity(3, i); diag_core_receipt(&state, 1001 + i * 10000, &receipt);
        assert(diag_core_trigger(&state, 1001 + i * 10000) == DIAG_CAPTURE_NONE);
    }
}
static void selective_kind(void) {
    init(false);
    for (unsigned i = 0; i < 20; ++i) {
        send(1, i, 1000 + i * 10000, 0x4007, 1);
        send(2, i, 1001 + i * 10000, 0, 1);
        send(3, i, 1002 + i * 10000, 0, 1);
    }
    assert(diag_core_trigger(&state, 191003) == DIAG_CAPTURE_KIND_STALL);
    assert(state.kind_last_ok_ms[1] == 191001 && state.kind_last_ok_ms[2] == 191002);
    send(1, 21, 192000, 0, 1);
    assert(diag_core_trigger(&state, 192001) == DIAG_CAPTURE_NONE);
}
static void episodes(void) {
    init(false); sample(10, 100000); send(1, 1, 1000, 0x400a, 1); sample(1002, 99900);
    send(1, 2, 2000, 0, 1); sample(3600000, 98000); send(1, 3, 3600100, 0x4007, 1);
    assert(state.episode_number == 2 && state.first_error.identity.seq == 1);
    assert(state.episode_first_error.identity.seq == 3 && state.episode_baseline.ms == 3600000);
    assert(state.baseline.ms == 10 && state.onset.ms == 1002 && !state.episode_onset.valid);
    assert(state.onset_sample_needed == 2); sample(3600200, 97000);
    assert(state.onset.ms == 1002 && state.episode_onset.ms == 3600200);
}
static void demand(void) {
    init(false);
    for (unsigned i = 0; i <= 180; ++i) diag_core_demand(&state, 1000 + i * 1000, true, false, 0, 999999, true);
    assert(diag_core_trigger(&state, 181000) == DIAG_CAPTURE_TX_STALL);
    assert(state.inhibit_until_ms == 999999 && state.voting);
    diag_core_demand(&state, 181001, false, false, 0, 0, false);
    assert(diag_core_trigger(&state, 181001) == DIAG_CAPTURE_NONE);
    for (unsigned i = 0; i <= 180; ++i) {
        send(1, i, 200000 + i * 1000, 0, 1);
        diag_core_demand(&state, 200000 + i * 1000, true, true, 190000, 999999, true);
    }
    diag_identity_t receipt = identity(3, 1); diag_core_receipt(&state, 190000, &receipt);
    assert(diag_core_trigger(&state, 380000) == DIAG_CAPTURE_DELIVERY_STALL);
    diag_core_receipt(&state, 379000, &receipt);
    diag_core_demand(&state, 380001, true, true, 379000, 999999, false);
    assert(diag_core_trigger(&state, 380001) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 500000) == DIAG_CAPTURE_NONE); /* obsolete demand */
}
static void recovery_receipt(void) {
    init(false);
    diag_core_recovery(&state, 1000, 2, 0, false, 0);
    assert(diag_core_trigger(&state, 1000) == DIAG_CAPTURE_RECOVERY);
    assert(state.events[0].type == DIAG_E_REC_REQUEST && state.events[0].code == 2);
    diag_core_recovery(&state, 1100, 2, DIAG_REC_TX, true, 0x400a);
    assert(state.events[1].type == DIAG_E_REC_RESULT && state.events[1].code == 0x400a);
    diag_identity_t id = identity(3, 22);
    diag_core_receipt(&state, 1200, &id); id.seq = 23; diag_core_receipt(&state, 30000, &id);
    assert(state.receipt_count == 2 && state.last_receipt.seq == 23 && state.event_count == 3);
    diag_core_receipt(&state, 100000, &id);
    assert(state.event_count == 4 && state.context.mqtt_pubacks == 0);
}
static void ring(void) {
    init(false); send(1, 1, 1000, 0x400a, 1);
    for (unsigned i = 1; i <= 100; ++i) send(1, i + 1, 1000 + i, i % 2 ? 0x4007 : 0x400a, 1);
    assert(state.event_count == DIAG_EVENT_COUNT && state.event_overwrites == 101-DIAG_EVENT_COUNT);
    assert(state.first_error.identity.seq == 1 && state.episode_first_error.identity.seq == 1);
    assert(state.first_seen_ms[1] == 1000 && state.first_seen_ms[2] == 1001);
    unsigned oldest = state.event_head;
    assert(state.events[oldest].identity.seq == 102-DIAG_EVENT_COUNT);
    state.counts[1] = UINT32_MAX; send(1, 102, 2000, 0x400a, 1);
    assert(state.counts[1] == UINT32_MAX);
}
static void root(void) {
    init(true); diag_identity_t id = identity(1, 100);
    diag_core_root(&state, 1000, &id, DIAG_ROOT_RX);
    diag_core_root(&state, 1001, &id, DIAG_ROOT_MQTT_ACCEPT);
    assert(state.peers[0].rx_identity.seq == 100 && state.peers[0].mqtt_accepted == 1);
    id.seq = 101; diag_core_root(&state, 2000, &id, DIAG_ROOT_RX);
    diag_core_root(&state, 2001, &id, DIAG_ROOT_BRIDGE_REJECT);
    assert(state.last_root_failure.seq == 101 && state.last_root_failure_stage == DIAG_ROOT_BRIDGE_REJECT);
    assert(diag_core_trigger(&state, 181999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 182000) == DIAG_CAPTURE_PEER_SILENT);
    diag_context_t c = state.context; c.topology.flags &= ~DIAG_F_ROOT;
    diag_core_context(&state, &c, 190000); c.topology.flags |= DIAG_F_ROOT;
    diag_core_context(&state, &c, 300000);
    assert(diag_core_trigger(&state, 500000) == DIAG_CAPTURE_NONE);
    diag_core_root(&state, 500001, &id, DIAG_ROOT_RX);
    assert(diag_core_trigger(&state, 680001) == DIAG_CAPTURE_PEER_SILENT);
    assert(state.root_counts[DIAG_ROOT_RX] == 3 && !state.context.mqtt_pubacks);
}
static void root_failure(void) {
    init(true); diag_identity_t id = identity(1, 1);
    for (unsigned i = 0; i <= 18; ++i) {
        id.seq = i + 1; diag_core_root(&state, 1000 + i * 10000, &id, DIAG_ROOT_RX);
        diag_core_root(&state, 1001 + i * 10000, &id, DIAG_ROOT_MQTT_FULL);
        diag_identity_t status = identity(3, i + 1);
        diag_core_root(&state, 1002 + i * 10000, &status, DIAG_ROOT_RX);
        diag_core_root(&state, 1003 + i * 10000, &status, DIAG_ROOT_MQTT_ACCEPT);
    }
    assert(diag_core_trigger(&state, 181004) == DIAG_CAPTURE_ROOT_FAILURE);
    assert(diag_core_trigger(&state, 241002) == DIAG_CAPTURE_NONE);
    diag_core_root(&state, 250000, &id, DIAG_ROOT_MQTT_ERROR);
    assert(state.peers[0].failure_since_ms == 250000);
    assert(diag_core_trigger(&state, 250001) == DIAG_CAPTURE_NONE);
    init(true); diag_core_recovery(&state, 1000, 5, 0, false, 0);
    assert(diag_core_trigger(&state, 1000) == DIAG_CAPTURE_RECOVERY);
    init(true); diag_core_recovery(&state, 1000, 6, 0, false, 0);
    assert(diag_core_trigger(&state, 1000) == DIAG_CAPTURE_RECOVERY);
}
static void root_downlink(void) {
    init(true); diag_identity_t incoming = identity(3, 1);
    for (unsigned i = 0; i <= 18; ++i) {
        uint64_t t = 1000 + i * 10000;
        incoming.seq = i + 1;
        diag_core_root(&state, t, &incoming, DIAG_ROOT_RX);
        diag_core_root(&state, t + 1, &incoming, DIAG_ROOT_MQTT_ACCEPT);
        diag_identity_t receipt = identity(7, i + 1);
        diag_core_tx_begin(&state, (t + 2) * 1000, &receipt, false);
        diag_core_tx_end(&state, (t + 2) * 1000 + 1, 0x4017);
    }
    assert(diag_core_trigger(&state, 181003) == DIAG_CAPTURE_KIND_STALL);
    assert(state.root_counts[DIAG_ROOT_RX] == 19 && state.root_counts[DIAG_ROOT_MQTT_ACCEPT] == 19);
    assert(state.kind_error_mask == (1U << 6) && !state.upstream_attempts);
    diag_identity_t receipt = identity(7, 20);
    diag_core_tx_begin(&state, UINT64_C(182000000), &receipt, false);
    diag_core_tx_end(&state, UINT64_C(182000001), 0);
    assert(diag_core_trigger(&state, 182001) == DIAG_CAPTURE_NONE);
    init(false); send(1, 1, 1000, 0x400a, 1); send(1, 2, 181000, 0x400a, 1);
    diag_context_t c = state.context; c.topology.flags |= DIAG_F_ROOT;
    diag_core_context(&state, &c, 181001);
    assert(diag_core_trigger(&state, 181002) == DIAG_CAPTURE_NONE);
    init(true); diag_identity_t config = identity(6, 1);
    diag_core_tx_begin(&state, UINT64_C(1000000), &config, false);
    diag_core_tx_end(&state, UINT64_C(1000001), 0x400a);
    for (uint64_t ms = 51000; ms <= 151000; ms += 50000) {
        diag_core_tx_begin(&state, ms * 1000, &config, false);
        diag_core_tx_end(&state, ms * 1000 + 1, 0x400a);
    }
    diag_core_tx_begin(&state, UINT64_C(181000000), &config, false);
    assert(diag_core_trigger(&state, 181001) == DIAG_CAPTURE_KIND_STALL);
}
static void role_clocks(void) {
    init(true);
    /* Even a stale/incorrect old Root demand input must not survive demotion. */
    diag_core_demand(&state, 1000, true, true, 0, 0, false);
    diag_core_demand(&state, 500000, true, true, 0, 0, false);
    diag_context_t c = state.context; c.topology.flags &= ~DIAG_F_ROOT; c.topology.layer = 2;
    diag_core_context(&state, &c, 500001);
    assert(!state.tx_demand && !state.delivery_demand && !state.kind_error_mask);
    assert(diag_core_trigger(&state, 500001) == DIAG_CAPTURE_NONE);
    diag_core_demand(&state, 500002, true, true, 0, 0, false);
    assert(diag_core_trigger(&state, 500003) == DIAG_CAPTURE_NONE);
    diag_identity_t id = identity(1, 1); diag_core_tx_begin(&state, UINT64_C(500004000), &id, true);
    assert(diag_core_trigger(&state, 500005) == DIAG_CAPTURE_NONE);

    init(false); sample(100, 100000); send(1, 1, 1000, 0, 1); send(1, 2, 2000, 0x400a, 1);
    diag_core_demand(&state, 3000, true, true, 1000, 0, false);
    c = state.context; c.topology.flags |= DIAG_F_ROOT;
    diag_core_context(&state, &c, 4000);
    assert(state.counts[0] == 1 && state.counts[1] == 1 && state.first_error.identity.seq == 2);
    assert(state.event_count == 1 && !state.kind_error_mask && !state.upstream_attempts);
    assert(!state.last_upstream_ok_ms && !state.last_upstream_ms && !state.first_upstream_ms);
    c.topology.flags &= ~DIAG_F_ROOT; diag_core_context(&state, &c, 600000);
    assert(diag_core_trigger(&state, 600001) == DIAG_CAPTURE_NONE);
    id = identity(1, 3); diag_core_tx_begin(&state, UINT64_C(600002000), &id, true);
    assert(state.first_upstream_ms == 600002 && state.upstream_attempts == 1);
    assert(diag_core_trigger(&state, 600003) == DIAG_CAPTURE_NONE);
    diag_core_tx_end(&state, UINT64_C(600003000), 0x4007);
    assert(diag_core_trigger(&state, 600004) == DIAG_CAPTURE_NONE);

    /* A real hung call is neither cancelled nor aged from previous successes. */
    init(false); id = identity(1, 1); diag_core_tx_begin(&state, UINT64_C(1000000), &id, true);
    c = state.context; c.topology.flags |= DIAG_F_ROOT; diag_core_context(&state, &c, 2000);
    assert(state.inflight && state.tx_start_us == 1000000 && state.active_identity.seq == 1);
    assert(diag_core_trigger(&state, 30999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 181000) == DIAG_CAPTURE_TX_STALL);
    c.topology.flags &= ~DIAG_F_ROOT; diag_core_context(&state, &c, 182000);
    assert(state.inflight && diag_core_trigger(&state, 182001) == DIAG_CAPTURE_TX_STALL);
    diag_core_tx_end(&state, UINT64_C(183000000), 0);
    assert(diag_core_trigger(&state, 183001) == DIAG_CAPTURE_TX_STALL);
}
static void idle_resume(void) {
    init(false); send(1, 1, 1000, 0, 1);
    diag_identity_t id = identity(1, 2);
    diag_core_tx_begin(&state, UINT64_C(600000000), &id, true);
    assert(state.upstream_attempts == 1 && state.first_upstream_ms == 600000);
    assert(!state.last_upstream_ok_ms && diag_core_trigger(&state, 600001) == DIAG_CAPTURE_RECEIPT_SILENT);
    diag_core_tx_end(&state, UINT64_C(600002000), 0x400a);
    assert(diag_core_trigger(&state, 600003) == DIAG_CAPTURE_RECEIPT_SILENT);
    assert(state.counts[0] == 1 && state.counts[1] == 1);
    send(1, 3, 1200000, 0x400a, 1);
    assert(state.kind_first_error_ms[0] == 1200000 && state.first_error.identity.seq == 2);
    assert(diag_core_trigger(&state, 1200001) == DIAG_CAPTURE_RECEIPT_SILENT);
    send(1, 4, 1800000, 0, 1);
    assert(diag_core_trigger(&state, 1800001) == DIAG_CAPTURE_RECEIPT_SILENT && !state.kind_error_mask);

    /* Long calls are activity. A new call shortly after their return is not
     * idle even though its start is far from the previous call's start. */
    init(false); send(1, 1, 1000, 0x400a, 100000000);
    assert(state.last_upstream_ms == 101000);
    id = identity(1, 2); diag_core_tx_begin(&state, UINT64_C(102000000), &id, true);
    assert(state.upstream_attempts == 2 && state.first_upstream_ms == 1000);
    diag_core_tx_end(&state, UINT64_C(202000000), 0x400a);
    assert(diag_core_trigger(&state, 202001) == DIAG_CAPTURE_TX_STALL);
    /* Real hang after idle is measured from the new SDK call start. */
    init(false); send(1, 1, 1000, 0, 1);
    id = identity(1, 2); diag_core_tx_begin(&state, UINT64_C(600000000), &id, true);
    assert(diag_core_trigger(&state, 629999) == DIAG_CAPTURE_RECEIPT_SILENT);
    assert(diag_core_trigger(&state, 630000) == DIAG_CAPTURE_TX_STALL);
}
static void idle_kind_inflight(void) {
    init(false); send(1, 1, 1000, 0x400a, 1);
    diag_identity_t id = identity(1, 2);
    diag_core_tx_begin(&state, UINT64_C(1000000000), &id, true);
    assert(!state.kind_error_mask && state.first_error.identity.seq == 1);
    assert(diag_core_trigger(&state, 1000001) == DIAG_CAPTURE_RECEIPT_SILENT);
    diag_core_tx_end(&state, UINT64_C(1000002000), 0);
    assert(diag_core_trigger(&state, 1000003) == DIAG_CAPTURE_RECEIPT_SILENT);
    send(1, 3, 1100000, 0x400a, 1);
    id = identity(1, 4); diag_core_tx_begin(&state, UINT64_C(2000000000), &id, true);
    assert(diag_core_trigger(&state, 2000001) == DIAG_CAPTURE_RECEIPT_SILENT);
    diag_core_tx_end(&state, UINT64_C(2000002000), 0x400a);
    assert(state.kind_first_error_ms[0] == 2000002);
    assert(diag_core_trigger(&state, 2000003) == DIAG_CAPTURE_RECEIPT_SILENT);

    init(true); id = identity(7, 1);
    diag_core_tx_begin(&state, UINT64_C(1000000), &id, false);
    diag_core_tx_end(&state, UINT64_C(1000001), 0x4017);
    id = identity(7, 2); diag_core_tx_begin(&state, UINT64_C(1000000000), &id, false);
    assert(!state.kind_error_mask && diag_core_trigger(&state, 1000001) == DIAG_CAPTURE_NONE);
    diag_core_tx_end(&state, UINT64_C(1000002000), 0x4017);
    assert(state.kind_first_error_ms[6] == 1000002);
    assert(diag_core_trigger(&state, 1000003) == DIAG_CAPTURE_NONE);
    id = identity(7, 3); diag_core_tx_begin(&state, UINT64_C(2000000000), &id, false);
    assert(diag_core_trigger(&state, 2000001) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 2029999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 2030000) == DIAG_CAPTURE_TX_STALL);
    diag_core_tx_end(&state, UINT64_C(2180001000), 0);
    assert(diag_core_trigger(&state, 2180002) == DIAG_CAPTURE_TX_STALL);
}
static void demand_gap(void) {
    init(false);
    diag_core_demand(&state, 1000, true, true, 0, 0, false);
    diag_core_demand(&state, 500000, true, true, 0, 0, false);
    assert(state.tx_demand_since_ms == 500000 && state.delivery_demand_since_ms == 500000);
    diag_identity_t receipt = identity(3, 1); diag_core_receipt(&state, 500000, &receipt);
    assert(diag_core_trigger(&state, 500001) == DIAG_CAPTURE_NONE);
    for (uint64_t ms = 501000; ms <= 680000; ms += 1000)
        diag_core_demand(&state, ms, true, true, 0, 0, false);
    assert(diag_core_trigger(&state, 680000) == DIAG_CAPTURE_TX_STALL);
    init(false); diag_identity_t id = identity(1, 1);
    diag_core_tx_begin(&state, UINT64_C(1000000), &id, true);
    diag_core_demand(&state, 1000, true, false, 0, 0, false);
    diag_core_demand(&state, 500000, true, false, 0, 0, false);
    assert(state.tx_demand_since_ms == 500000);
    assert(diag_core_trigger(&state, 500001) == DIAG_CAPTURE_TX_STALL); /* real call age remains */
}
static void peers(void) {
    init(true);
    for (unsigned i = 0; i < 5; ++i) {
        diag_identity_t id = identity(3, i); id.node_id[0] = (char)('0' + i);
        diag_core_root(&state, 1000 + i, &id, DIAG_ROOT_RX);
    }
    assert(state.peer_overflows == 1 && state.root_counts[DIAG_ROOT_RX] == 5);
    for (unsigned i = 0; i < 4; ++i) assert(state.peers[i].rx_identity.seq == i);
    diag_core_root(&state, 3000, NULL, DIAG_ROOT_WIRE_BAD);
    assert(!state.last_root_failure.valid && state.last_root_failure_stage == DIAG_ROOT_WIRE_BAD);
}
static void time64(void) {
    init(false); uint64_t t = UINT64_C(9000000000);
    state.observation_start_ms = t;
    for (unsigned i = 0; i <= 180; ++i) diag_core_demand(&state, t + i * 1000, true, false, 0, 0, false);
    assert(diag_core_trigger(&state, t + 180000) == DIAG_CAPTURE_TX_STALL);
    send(1, 1, t + 180000, 0, 1);
    assert(diag_core_trigger(&state, t + 180001) == DIAG_CAPTURE_NONE);
    diag_core_capture(&state, t + 180002, DIAG_CAPTURE_FOLLOWUP);
    assert(state.capture_ms == t + 180002 && state.capture_reason == DIAG_CAPTURE_FOLLOWUP);
}
static void receipt_clock(void) {
    init(false); state.observation_start_ms = 1000000;
    assert(diag_core_trigger(&state, 1299999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 1300000) == DIAG_CAPTURE_RECEIPT_SILENT);
    diag_identity_t id = identity(3, 1); diag_core_receipt(&state, 1300001, &id);
    assert(diag_core_trigger(&state, 1600000) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 1600001) == DIAG_CAPTURE_RECEIPT_SILENT);
    diag_context_t c = state.context; c.topology.flags |= DIAG_F_ROOT;
    diag_core_context(&state, &c, 1600002);
    assert(diag_core_trigger(&state, 9000000) == DIAG_CAPTURE_NONE);
    c.topology.flags &= ~DIAG_F_ROOT; diag_core_context(&state, &c, 9000000);
    assert(state.role_changed_ms == 9000000);
    assert(diag_core_trigger(&state, 9299999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 9300000) == DIAG_CAPTURE_RECEIPT_SILENT);
    state.arm_generation = 1; state.observation_start_ms = 10000000;
    assert(diag_core_trigger(&state, 10299999) == DIAG_CAPTURE_NONE);
    assert(diag_core_trigger(&state, 10300000) == DIAG_CAPTURE_RECEIPT_SILENT);
}
static void parser(void) {
    const char *good[] = {
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":4294967295}",
        " { \"meta\":{\"seq\":2,\"boot_id\":\"bad\"}, \"seq\":0, \"boot_id\":\"0123456789abcdef\" } ",
        "{\"x\":[null,true,false,-1.2e+3,\"a\\\"b\"],\"seq\":12,\"boot_id\":\"0123456789abcdef\"}",
    };
    for (unsigned i = 0; i < sizeof(good)/sizeof(good[0]); ++i) {
        diag_identity_t id; diag_identity_read(&id, 1, node, good[i], strlen(good[i])); assert(id.valid);
    }
    const char *bad[] = {
        "", "{}", "[]", "{\"meta\":{\"seq\":1,\"boot_id\":\"0123456789abcdef\"}}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":4294967296}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":-1}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1.0}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1e1}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":01}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"seq\":2}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"\\u0073eq\":2}",
        "{\"boot_id\":\"0123456789abcdeg\",\"seq\":1}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1}garbage",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"x\":[}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,}",
        "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"x\":blah}",
    };
    for (unsigned i = 0; i < sizeof(bad)/sizeof(bad[0]); ++i) {
        diag_identity_t id; diag_identity_read(&id, 1, node, bad[i], strlen(bad[i])); assert(!id.valid);
    }
    char bounded[1024]; memset(bounded, 'x', sizeof(bounded)); diag_identity_t id;
    diag_identity_read(&id, 1, node, bounded, sizeof(bounded)); assert(!id.valid);
    diag_identity_read(&id, 1, "", good[0], strlen(good[0])); assert(!id.valid);
    /* Every truncated prefix: no string terminator is available to the parser. */
    size_t n = strlen(good[0]);
    for (size_t i = 0; i < n; ++i) {
        char *part = malloc(i ? i : 1); assert(part); memcpy(part, good[0], i);
        diag_identity_read(&id, 1, node, part, i); assert(!id.valid); free(part);
    }
}
static void buckets(void) {
    const int32_t values[] = {0, 0x400a, 0x4007, 0x400d, 0x4017, 0x400b, 0x400f, 0x4012};
    for (unsigned i = 0; i < 8; ++i) assert(diag_error_bucket(values[i]) == i);
    assert(diag_error_bucket(-1) == 7 && diag_error_bucket(0x101) == 7);
}
static void dump(void) {
    first_and_transitions(); diag_core_capture(&state, 20000, DIAG_CAPTURE_FOLLOWUP);
    assert(fwrite(&state, 1, sizeof(state), stdout) == sizeof(state));
}
/* SYNTHETIC evidence only: real core, invented events, no device or Gateway. */
static void synthetic_dump(void) {
    init(false); sample(100, 100000); send(1, 1, 1000, 0, 100);
    send(1, 2, 10000, 0x400a, 5000000); sample(15001, 90000);
    for (unsigned i = 3; i <= 20; ++i) send(1, i, (i - 1) * 10000, 0x4007, 10);
    assert(diag_core_trigger(&state, 195001) == DIAG_CAPTURE_KIND_STALL);
    diag_core_capture(&state, 195001, DIAG_CAPTURE_KIND_STALL);
    assert(fwrite(&state, 1, sizeof(state), stdout) == sizeof(state));
    diag_context_t c = state.context;
    c.recovery_requests = 1; diag_core_context(&state, &c, 200000);
    diag_core_recovery(&state, 200000, 2, DIAG_REC_TX | DIAG_REC_DELIVERY, false, 0);
    c.recovery_ok = 1; diag_core_context(&state, &c, 200100);
    diag_core_recovery(&state, 200100, 2, DIAG_REC_TX | DIAG_REC_DELIVERY, true, 0);
    c.topology.flags &= ~DIAG_F_PARENT; c.topology.layer = -1;
    memset(c.topology.parent, 0, 6); memset(c.topology.root, 0, 6); c.parent_disconnects = 1;
    diag_core_context(&state, &c, 200200); diag_core_event(&state, 200200, DIAG_E_MESH, 8, 0);
    c.topology.flags |= DIAG_F_PARENT; c.topology.layer = 2; memcpy(c.topology.parent, "BSSID2", 6);
    c.recovery_link = 1; diag_core_context(&state, &c, 201000); diag_core_event(&state, 201000, DIAG_E_MESH, 7, 0);
    memcpy(c.topology.root, "ROOT02", 6); c.root_changes = 1;
    diag_core_context(&state, &c, 202000); diag_core_event(&state, 202000, DIAG_E_MESH, 14, 0);
    send(1, 21, 210000, 0x4007, 10); sample(211000, 89000);
    send(1, 22, 220000, 0, 100);
    diag_identity_t status = identity(3, 9); diag_core_receipt(&state, 221000, &status);
    for (unsigned i = 23; i <= 43; ++i) send(1, i, i * 10000, 0, 100);
    sample(430001, 93000); diag_core_capture(&state, 435001, DIAG_CAPTURE_FOLLOWUP);
    assert(state.first_error.code == 0x400a && state.first_seen_ms[2] == 20000);
    assert(state.context.recovery_ok == 1 && state.last_receipt.seq == 9 && state.last_ok_identity.seq == 43);
    assert(!state.context.mqtt_pubacks && state.event_overwrites > 0);
    assert(fwrite(&state, 1, sizeof(state), stdout) == sizeof(state));
}
int main(int argc, char **argv) {
    assert(argc == 2);
    struct { const char *name; void (*run)(void); } cases[] = {
        {"normal",normal},{"transitions",first_and_transitions},{"sustained",sustained},
        {"inflight",inflight},{"occasional",occasional_success},{"kind",selective_kind},
        {"episodes",episodes},{"demand",demand},{"recovery",recovery_receipt},
        {"ring",ring},{"root",root},{"root_failure",root_failure},{"root_downlink",root_downlink},{"role_clocks",role_clocks},{"idle_resume",idle_resume},{"idle_kind_inflight",idle_kind_inflight},{"demand_gap",demand_gap},{"peers",peers},{"time64",time64},
        {"receipt_clock",receipt_clock},{"parser",parser},{"buckets",buckets},{"dump",dump},{"synthetic_dump",synthetic_dump},
    };
    for (unsigned i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i)
        if (!strcmp(argv[1],cases[i].name)) { cases[i].run(); return 0; }
    return 2;
}
