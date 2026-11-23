#include "mesh_diag.h"
#include "ble_scan_runtime.h"
#include "mesh_probe.h"
#if OMK_DIAGNOSTICS
#include <string.h>
#include <limits.h>
#include "mesh_transport.h"
#include "esp_mesh.h"
#include "native_recovery.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

_Static_assert(ESP_ERR_MESH_TIMEOUT == 0x400a && ESP_ERR_MESH_NO_MEMORY == 0x4007 &&
    ESP_ERR_MESH_QUEUE_FULL == 0x400d && ESP_ERR_MESH_XMIT == 0x4017 &&
    ESP_ERR_MESH_DISCONNECTED == 0x400b && ESP_ERR_MESH_NO_ROUTE_FOUND == 0x400f,
    "Diagnostic mapping requires ESP-IDF 6.0.1 Mesh errors");
_Static_assert(REC_CHILD_RESELECT == 2 && REC_MQTT_RECONNECT == 5 && REC_MQTT_RECREATE == 6 && REC_CHILD_REPAIR == 7,
    "Diagnostic mapping requires audited AB5/V2 recovery action values");
static portMUX_TYPE diag_lock = portMUX_INITIALIZER_UNLOCKED;
static _Alignas(8) diag_core_t live, capture;
static bool initialized, store_initialized;
/* These variables and capture are owned exclusively by the USB task. */
static uint64_t last_poll_ms, last_sample_ms, first_save_ms;
static unsigned save_attempts;
static uint64_t slot_ms[2];
static unsigned slot_priority[2];
static bool save_failed;
static int pinned_slot = -1;
static uint64_t healthy_since_ms;
static bool fault_interval;
#define SAVE_REFRESH_MS UINT64_C(900000)
static uint32_t capture_token;
static bool capture_v2;
_Static_assert(sizeof(capture) <= DIAG_STORE_PAYLOAD_MAX, "diagnostic slot capacity");
_Static_assert(sizeof(mesh_probe_core_t)<=sizeof(capture),"reuse existing USB snapshot RAM");

static uint64_t clock_us(void) { return (uint64_t)esp_timer_get_time(); }
static void read_context(diag_context_t *out) {
    mesh_transport_status_t v;
    mesh_transport_get_status(&v);
    memset(out, 0, sizeof(*out));
    out->topology.flags = (v.started ? DIAG_F_STARTED : 0) | (v.parent_connected ? DIAG_F_PARENT : 0) |
        (v.is_root ? DIAG_F_ROOT : 0) | (v.has_ip ? DIAG_F_IP : 0) |
        (v.mqtt_connected ? DIAG_F_MQTT : 0) | (v.rootless ? DIAG_F_ROOTLESS : 0);
    out->topology.layer = v.layer;
    memcpy(out->topology.parent, v.parent_mac, 6);
    memcpy(out->topology.root, v.root_mac, 6);
    out->tx_accepted = v.tx_accepted; out->tx_dropped = v.tx_dropped;
    out->rx_dropped = v.rx_dropped; out->mesh_rx_ok = v.mesh_rx_ok;
    out->mesh_rx_errors = v.mesh_rx_errors; out->mqtt_published = v.mqtt_published;
    out->parent_disconnects = v.parent_disconnect_count; out->root_changes = v.root_change_count;
    out->recovery_requests = v.recovery_requests; out->recovery_ok = v.recovery_ok;
    out->recovery_link = v.recovery_link; out->mqtt_pubacks = v.mqtt_pubacks;
}
void mesh_diag_boot(const char *node_id, const char *boot_id, uint32_t reset_reason) {
    mesh_probe_boot(node_id,boot_id);
    portENTER_CRITICAL(&diag_lock);
    diag_core_init(&live, node_id, boot_id, "BLE-AB1-V2D-P1", reset_reason);
    live.observation_start_ms = clock_us() / 1000;
    initialized = true;
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_scan(uint64_t ms,uint32_t epoch,uint32_t detail) {
    portENTER_CRITICAL(&diag_lock);
    if(initialized) diag_core_event(&live,ms,DIAG_E_BLE_SCAN,epoch,detail);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_request(unsigned kind) {
    if (kind < 1 || kind > 7) return;
    portENTER_CRITICAL(&diag_lock);
    if (live.request_count[kind - 1] != UINT32_MAX) ++live.request_count[kind - 1];
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_ble(unsigned type, bool event, bool rejected, bool suppressed) {
    if (type > 3) return;
    portENTER_CRITICAL(&diag_lock);
    /* Choose one counter only. Failures are additionally counted by the caller. */
    uint32_t value = suppressed ? live.ble_suppressed[type] : rejected ? live.ble_rejected[type] :
        event ? live.ble_event[type] : live.ble_periodic[type];
    if (value != UINT32_MAX) ++value;
    if (suppressed) live.ble_suppressed[type] = value;
    else if (rejected) live.ble_rejected[type] = value;
    else if (event) live.ble_event[type] = value;
    else live.ble_periodic[type] = value;
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_state(char boot_id[17], uint64_t *start, uint32_t *generation, uint32_t *reason) {
    portENTER_CRITICAL(&diag_lock);
    memcpy(boot_id, live.boot_id, 17);
    *start = live.observation_start_ms; *generation = live.arm_generation;
    *reason = diag_core_trigger(&live, clock_us() / 1000);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_tx_begin(const mesh_wire_message_t *message, bool upstream) {
    diag_identity_t id;
    diag_context_t context;
    diag_identity_read(&id, message->kind, message->node_id, message->payload, message->length);
    read_context(&context);
    uint64_t now = clock_us();
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now / 1000);
    diag_core_tx_begin(&live, now, &id, upstream);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_tx_end(int32_t result) {
    uint64_t now = clock_us(); /* Exclude diagnostic bookkeeping from API duration. */
    diag_context_t context;
    read_context(&context);
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now / 1000);
    diag_core_tx_end(&live, now, result);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_mesh_event(int32_t id) {
    switch (id) {
    case MESH_EVENT_STARTED: case MESH_EVENT_STOPPED: case MESH_EVENT_PARENT_CONNECTED:
    case MESH_EVENT_PARENT_DISCONNECTED: case MESH_EVENT_LAYER_CHANGE: case MESH_EVENT_ROOT_ADDRESS:
    case MESH_EVENT_NETWORK_STATE: case MESH_EVENT_TODS_STATE: case MESH_EVENT_VOTE_STARTED:
    case MESH_EVENT_VOTE_STOPPED: case MESH_EVENT_ROOT_SWITCH_REQ: case MESH_EVENT_ROOT_SWITCH_ACK:
        break;
    default: return;
    }
    diag_context_t context;
    read_context(&context);
    uint64_t now = clock_us() / 1000;
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now);
    diag_core_event(&live, now, DIAG_E_MESH, id, 0);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_disconnected(const uint8_t bssid[6], uint8_t reason, int8_t rssi) {
    uint64_t now = clock_us() / 1000;
    portENTER_CRITICAL(&diag_lock);
    live.disconnect_ms = now;
    memcpy(live.disconnected_parent, bssid, 6);
    live.disconnect_reason = reason;
    live.disconnect_rssi = rssi;
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_quality(bool bad) {
    portENTER_CRITICAL(&diag_lock);
    live.quality_bad = bad;
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_demand(bool tx, bool delivery, uint64_t proven_ms, uint64_t inhibit_until, bool voting) {
    diag_context_t context;
    read_context(&context);
    uint64_t now = clock_us() / 1000;
    bool child = !(context.topology.flags & DIAG_F_ROOT);
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now);
    diag_core_demand(&live, now, child && tx, child && delivery, proven_ms, inhibit_until, voting);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_recovery(uint32_t action, uint32_t reasons, bool finished, int32_t result) {
    diag_context_t context;
    read_context(&context);
    uint64_t now = clock_us() / 1000;
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now);
    diag_core_recovery(&live, now, action, reasons, finished, result);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_receipt(const char *node_id, const char *boot_id, uint32_t seq) {
    diag_identity_t id = {.kind = MESH_MESSAGE_NODE_STATUS, .valid = 1, .seq = seq};
    memcpy(id.node_id, node_id, 13); memcpy(id.boot_id, boot_id, 17);
    uint64_t now = clock_us() / 1000;
    portENTER_CRITICAL(&diag_lock);
    diag_core_receipt(&live, now, &id);
    portEXIT_CRITICAL(&diag_lock);
}
void mesh_diag_root(const mesh_wire_message_t *message, unsigned stage) {
    diag_identity_t id = {0};
    if (message) {
        /* Individual peer evidence is limited to SEN66/status. BLE remains aggregate only. */
        if (message->kind == MESH_MESSAGE_SEN66 || message->kind == MESH_MESSAGE_NODE_STATUS)
            diag_identity_read(&id, message->kind, message->node_id, message->payload, message->length);
        else { id.kind = message->kind; memcpy(id.node_id, message->node_id, 13); }
    }
    uint64_t now = clock_us() / 1000;
    portENTER_CRITICAL(&diag_lock);
    diag_core_root(&live, now, &id, stage);
    portEXIT_CRITICAL(&diag_lock);
}
static void take_sample(uint64_t now) {
    diag_sample_t sample = {0};
    mesh_transport_status_t view;
    mesh_transport_get_status(&view);
    sample.ms = now;
    mesh_tx_pending_t tx = {0}; mesh_rx_pending_t rx = {0};
    sample.tx_result = view.started ? esp_mesh_get_tx_pending(&tx) : ESP_ERR_INVALID_STATE;
    sample.rx_result = view.started ? esp_mesh_get_rx_pending(&rx) : ESP_ERR_INVALID_STATE;
    sample.tx_pending[0] = tx.to_parent; sample.tx_pending[1] = tx.to_parent_p2p;
    sample.tx_pending[2] = tx.to_child; sample.tx_pending[3] = tx.to_child_p2p;
    sample.tx_pending[4] = tx.mgmt; sample.tx_pending[5] = tx.broadcast;
    sample.rx_pending[0] = rx.toDS; sample.rx_pending[1] = rx.toSelf;
    const uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    sample.heap_free = heap_caps_get_free_size(caps);
    sample.heap_min = heap_caps_get_minimum_free_size(caps);
    sample.heap_largest = heap_caps_get_largest_free_block(caps);
    portENTER_CRITICAL(&diag_lock);
    diag_core_sample(&live, &sample);
    portEXIT_CRITICAL(&diag_lock);
    last_sample_ms = now;
}
static void freeze(uint64_t now, uint32_t reason) {
    portENTER_CRITICAL(&diag_lock);
    diag_core_capture(&live, now, reason);
    capture = live;
    capture_v2=false;
    portEXIT_CRITICAL(&diag_lock);
    /* Separate locks: the probe includes pre/post SDK calls, recovery phases,
     * and the independent one-second ring, each with its own timestamp. */
    mesh_probe_copy((uint8_t *)&capture + offsetof(diag_core_t, probe));
    ++capture_token;
    if (!capture_token) ++capture_token;
}
static unsigned priority(uint32_t reason) {
    if (!reason) return 0;
    if (reason == DIAG_CAPTURE_ONSET || reason == DIAG_CAPTURE_RECOVERY) return 1;
    return 2;
}
static int save_slot(uint64_t now, unsigned importance, diag_store_status_t storage) {
    if (!importance || diag_store_block_reason() == 1) return -1;
    /* One provisional onset, with the second sector reserved for a measured
     * fault. A recovery request alone never spends both sectors. */
    if (!storage.attempt_mask && storage.ready) return 0;
    if (importance < 2) return -1;
    if (pinned_slot >= 0) {
        unsigned moving = (unsigned)pinned_slot ^ 1U;
        uint64_t latest = slot_ms[moving] > slot_ms[pinned_slot] ? slot_ms[moving] : slot_ms[pinned_slot];
        if (now - latest < SAVE_REFRESH_MS) return -1;
        if (!(storage.attempt_mask & (1U << moving)) && storage.ready) return (int)moving;
        if (!save_failed && storage.valid_mask == 3) return (int)moving;
        return -1;
    }
    for (unsigned i = 0; i < 2; ++i)
        if (!(storage.attempt_mask & (1U << i)) && storage.ready) return (int)i;
    if (!save_failed && storage.valid_mask == 3)
        return slot_ms[0] <= slot_ms[1] ? 0 : 1;
    return -1;
}
void mesh_diag_poll(void) {
    if (!initialized) return;
    mesh_probe_poll(); /* independent of sender/MQTT, even when flash protected */
    if (!store_initialized) { diag_store_init(); store_initialized = true; }
    uint64_t now = clock_us() / 1000;
    if (last_poll_ms && now - last_poll_ms < 1000) return;
    last_poll_ms = now;
    diag_context_t context;
    read_context(&context);
    portENTER_CRITICAL(&diag_lock);
    diag_core_context(&live, &context, now);
    uint32_t reason = diag_core_trigger(&live, now);
    bool onset = live.onset_sample_needed != 0;
    /* A periodic sample can consume onset_sample_needed when an error arrives
     * during its SDK getters. The durable first-error latch must still cause
     * the next poll to save, even after that flag has been consumed. */
    if (!reason && (onset || (live.first_error_valid && !save_attempts))) reason = DIAG_CAPTURE_ONSET;
    portEXIT_CRITICAL(&diag_lock);
    diag_store_status_t storage = diag_store_status();
    /* A fault interval ends after 60 continuous seconds without a priority-2
     * condition. Brief reconnects must not retire the pinned onset. */
    if (priority(reason) == 2) {
        fault_interval = true; healthy_since_ms = 0;
    } else if (fault_interval) {
        if (!healthy_since_ms) healthy_since_ms = now;
        if (now - healthy_since_ms >= 60000) {
            fault_interval = false; pinned_slot = -1; healthy_since_ms = 0;
        }
    }
    int selected = save_slot(now, priority(reason), storage);
    bool due = selected >= 0;
    /* Audited IDF getters read queue counts, without waiting for the sender.
     * Sample before freezing, so current/onset do not describe a minute ago. */
    if (!last_sample_ms || now - last_sample_ms >= 10000 || onset || due) take_sample(now);
    if (due) {
        portENTER_CRITICAL(&diag_lock);
        ++live.capture_sequence;
        diag_core_event(&live, now, DIAG_E_IO_BEGIN, selected, live.capture_sequence);
        portEXIT_CRITICAL(&diag_lock);
        freeze(now, reason);
        unsigned slot = (unsigned)selected;
        ++save_attempts;
        if (save_attempts == 1) first_save_ms = now;
        esp_err_t result = (storage.attempt_mask & (1U << slot)) ?
            diag_store_replace(slot, &capture, sizeof(capture)) :
            diag_store_save(slot, &capture, sizeof(capture));
        slot_ms[slot] = now; slot_priority[slot] = priority(reason);
        if (result == ESP_OK && priority(reason) == 2 && pinned_slot < 0) pinned_slot = (int)slot;
        save_failed = result != ESP_OK; /* no unbounded I/O retries */
        uint32_t failure_phase = diag_store_phase();
        storage = diag_store_status();
        portENTER_CRITICAL(&diag_lock);
        if (result != ESP_OK && !live.save_failure_slot_plus1) {
            live.save_failure_slot_plus1 = slot + 1;
            live.save_failure_phase = failure_phase;
            live.save_failure_error = result;
        }
        diag_core_event(&live, clock_us() / 1000, DIAG_E_IO_END, result, storage.last_io_us);
        portEXIT_CRITICAL(&diag_lock);
    }
    portENTER_CRITICAL(&diag_lock);
    live.recovery_trigger = 0; /* request is an event, not a permanent fault */
    portEXIT_CRITICAL(&diag_lock);
    portENTER_CRITICAL(&diag_lock);
    uint32_t free_heap=live.current.heap_free,min_heap=live.current.heap_min,arm=live.arm_generation;
    portEXIT_CRITICAL(&diag_lock);
    uint8_t masks=(storage.ready?1:0) | ((storage.occupied_mask&3)<<1) |
                  ((storage.valid_mask&3)<<3) | ((storage.attempt_mask&3)<<5);
    uint8_t phase=(diag_store_phase()&15) | ((diag_store_block_reason()&15)<<4);
    ab_runtime_diagnostic(free_heap,min_heap,arm,masks,phase,storage.error?1:0);
}
uint32_t mesh_diag_live_begin(size_t *length) {
    freeze(clock_us() / 1000, DIAG_CAPTURE_NONE);
    *length = sizeof(capture);
    return capture_token;
}
uint32_t mesh_diag_live_crc32(void) {
    return diag_store_crc32(&capture, capture_v2?sizeof(mesh_probe_core_t):sizeof(capture));
}
uint32_t mesh_diag_v2_begin(size_t *length) {
    mesh_probe_copy(&capture); capture_v2=true;
    ++capture_token; if(!capture_token) ++capture_token;
    *length=sizeof(mesh_probe_core_t); return capture_token;
}
esp_err_t mesh_diag_v2_read(uint32_t token,uint32_t offset,void *out,size_t length) {
    if(!capture_v2 || !token || token!=capture_token || offset>sizeof(mesh_probe_core_t) || length>sizeof(mesh_probe_core_t)-offset)
        return ESP_ERR_INVALID_STATE;
    memcpy(out,(const uint8_t *)&capture+offset,length); return ESP_OK;
}
esp_err_t mesh_diag_live_read(uint32_t token, uint32_t offset, void *out, size_t length) {
    if (capture_v2 || !token || token != capture_token || offset > sizeof(capture) || length > sizeof(capture) - offset)
        return ESP_ERR_INVALID_ARG;
    memcpy(out, (const uint8_t *)&capture + offset, length);
    return ESP_OK;
}
esp_err_t mesh_diag_rearm(void) {
    esp_err_t error = diag_store_arm();
    if (error == ESP_OK) {
        /* New observation interval: old fault latches must not consume fresh slots.
         * Existing transport counters and all recovery state remain untouched. */
        char node_id[13], boot_id[17];
        portENTER_CRITICAL(&diag_lock);
        memcpy(node_id, live.node_id, sizeof(node_id));
        memcpy(boot_id, live.boot_id, sizeof(boot_id));
        uint32_t reset_reason = live.reset_reason;
        uint32_t generation = live.arm_generation + 1;
        if (!generation) generation = 1;
        /* Keep an SDK call crossing arm visible. Its real duration is evidence;
         * trigger evaluation grants the new window a fresh 30 seconds. */
        diag_identity_t active = live.active_identity;
        uint64_t started = live.tx_start_us;
        uint32_t inflight = live.inflight, upstream = live.upstream;
        diag_core_init(&live, node_id, boot_id, "BLE-AB1-V2D-P1", reset_reason);
        live.observation_start_ms = clock_us() / 1000;
        live.arm_generation = generation;
        live.inflight = live.inflight_before_arm = inflight;
        live.active_identity = active; live.tx_start_us = started; live.upstream = upstream;
        portEXIT_CRITICAL(&diag_lock);
        save_attempts = 0; first_save_ms = 0; last_sample_ms = 0;
        memset(slot_ms, 0, sizeof(slot_ms)); memset(slot_priority, 0, sizeof(slot_priority));
        save_failed = false; pinned_slot = -1; fault_interval = false; healthy_since_ms = 0;
        ++capture_token; /* Invalidate a concurrent host's previous live export. */
        if (!capture_token) ++capture_token;
    }
    return error;
}
#endif
