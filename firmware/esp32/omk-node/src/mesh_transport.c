#include "mesh_diag.h"
#include "mesh_transport.h"
#include "discovery_ble.h"
#include "mesh_tx_pacing.h"
#include "ble_scan_runtime.h"
#include "mesh_quality.h"
#include "mesh_probe.h"
#include "esp_random.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_mesh.h"
#include "esp_netif.h"
#include "esp_netif_net_stack.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/dhcp.h"
#include "gateway_credentials.h"
#include "mesh_credentials.h"
#include "mesh_session.h"
#include "native_recovery.h"
#include "mqtt_client.h"
#include "node_identity.h"
#include "node_messages.h"

/* These queues, the SDK Mesh queues and the bounded MQTT outbox are the only
 * transport storage. Status receipts prove Root admission only; they are never
 * retried and do not acknowledge MQTT/Gateway delivery or other data records. */
#define LOCAL_DATA_COUNT 3
#define LOCAL_STATUS_COUNT 1
#define ROOT_DATA_COUNT 4
/* Root plus the three deployed children can report in the same burst. */
#define ROOT_STATUS_COUNT 4
#define VOTE_HOLD_MAX_MS 300000U
#define CONTROL_COUNT 2
#define RECEIPT_COUNT 8
#define RECEIPT_PAYLOAD_MAX 96
#define ROUTE_COUNT CONFIG_MESH_ROUTE_TABLE_SIZE
#define ROUTE_LIFETIME_MS 120000U
#define MQTT_OUTBOX_BYTES 8192U
#define MQTT_DATA_BYTES 4096U
#define MQTT_TOPIC_SIZE 96
#define LEASE_CHECK_INTERVAL_MS 1000U
#define MQTT_BROKER_URI "mqtt://192.168.50.1:1883"

static const char *TAG = "omk-transport";

typedef struct {
    native_recovery_action_t action;
    uint32_t epoch, mqtt_generation, parent_connection;
    uint64_t progress_ms;
    bool repeated_loss;
    bool tx_stall, delivery_stall, quality_bad;
    uint32_t tx_progress, delivery_progress, local_generation;
    uint8_t parent_bssid[6];
} recovery_request_t;

typedef struct {
    mesh_wire_message_t message;
    uint8_t source[6];
    uint64_t queued_ms;
    uint32_t epoch;
    uint32_t local_generation;
    bool local;
} publication_t;

typedef struct {
    mesh_wire_message_t message;
    uint8_t peer[6];
    bool scan;
    uint64_t queued_ms;
    uint32_t epoch;
} control_t;

typedef struct {
    uint64_t uptime_s;
    uint32_t seq;
    char boot_id[17];
} status_identity_t;

typedef struct {
    status_identity_t identity;
    uint64_t queued_ms;
    uint32_t epoch;
    uint8_t peer[6];
    char node_id[13];
} status_receipt_t;

typedef struct {
    bool offered, submitted, confirmed;
    char boot_id[17];
    uint32_t epoch, generation, first_seq, last_sent_seq, ack_seq, progress;
    uint64_t offer_ms, first_uptime_s, last_sent_uptime_s, proven_ms;
} delivery_state_t;

typedef struct {
    mesh_transport_status_t status; /* Counters, layer and parent MAC. */
    mesh_session_t session;
    uint8_t root_mac[6]; /* Confirmed for the current parent/tree, or zero. */
    uint8_t last_root_mac[6]; /* Last confirmed Root, for change counting only. */
    bool lease_observation_ready; /* DHCP reset for this parent has completed. */
    uint32_t local_generation;
    esp_mqtt_client_handle_t active_client;
    uint32_t client_epoch;
    bool subscribe_pending;
    bool voting;
    bool recovery_wait_link, vote_wait_demotion;
    bool recovery_link_root;
    uint32_t parent_connection, recovery_link_generation;
    uint64_t recovery_link_until;
    uint8_t recovery_parent_bssid[6];
    uint64_t inhibit_until, vote_observe_until, vote_hold_until, last_puback_ms, qos1_demand_ms;
    bool qos1_pending;
    uint64_t mqtt_disconnected_ms;
    uint32_t mqtt_generation;
    /* Bounded local liveness evidence, never an application delivery ACK.
     * Producers record demand even when the local queue rejects their record;
     * the sender marks an upstream SDK call before entering that blocking API. */
    bool tx_offered, tx_inflight;
    uint64_t tx_offer_ms;
    uint32_t tx_offer_epoch, tx_offer_generation, tx_progress;
    delivery_state_t delivery;
    uint32_t delivery_generation;
    /* Last three weak losses of this role and BSSID, independent of epochs.
     * Expiry of the five-minute window is also the stability reset. */
    uint64_t parent_loss_ms[3];
    unsigned parent_losses;
    uint8_t loss_bssid[6];
} transport_state_t;

typedef struct {
    bool used;
    char node_id[13];
    uint8_t mac[6];
    uint32_t revision;
    uint64_t seen_ms;
} origin_route_t;

typedef struct {
    esp_mqtt_client_handle_t client;
    uint32_t epoch;
    bool started;
    mesh_config_assembler_t assembler;
    uint64_t fragment_queued_ms;
} mqtt_context_t;

static portMUX_TYPE state_lock = portMUX_INITIALIZER_UNLOCKED;
static transport_state_t state;
/* Separate from snapshots: bounded proof storage must not enlarge worker stacks.
 * Keep the first send in each 5 s bucket for 125 s; bursts cannot evict it.
 * Seven additional recent identities cover off-cycle sends. Overflow only
 * discards extra proofs, never upstream records or protected periodic proofs. */
#define PROOF_BUCKETS 25U
#define PROOF_EXTRA 7U
typedef struct { bool used; uint32_t seq; uint64_t uptime_s, bucket; } sent_proof_t;
static sent_proof_t sent_proofs[PROOF_BUCKETS + PROOF_EXTRA];
static unsigned proof_extra;
static bool initialized;
static uint32_t scan_observation;
static uint64_t root_nonce; /* guarded by state_lock; MQTT connection tenure */
static mesh_tx_pacing_t tx_pacing; /* mesh_sender only, never copied into task snapshots */
/* Separate bounded quality window: never alter AB boot cumulative banks. */
static mesh_quality_t quality;
/* Include kind 8/downlinks in physical serialization, not ordinary progress. */
static bool sdk_busy, recovery_gate;
enum { REPAIR_IDLE, REPAIR_QUIET, REPAIR_DISCONNECT, REPAIR_RESTORE, REPAIR_LINK, REPAIR_VERIFY, REPAIR_LATCHED };
/* MQTT owner only. No API or wait in the SDK event callback. */
static struct {
    recovery_request_t request;
    unsigned phase, restores, subtree_nodes;
    bool self_disabled, connect_needed, accepted;
    uint32_t disconnects, connection, root_changes;
    uint64_t deadline, retry_after, healthy_since;
    int32_t result;
} repair;
static esp_netif_t *netif_sta;
static char own_id[13];
static uint8_t own_mac[6];
static QueueHandle_t local_data, local_status, local_registration;
static QueueHandle_t root_data, root_status, root_registration;
static QueueHandle_t broker_configs, mesh_configs, apply_configs;
static QueueHandle_t status_receipts;
static TaskHandle_t rx_task_handle, tx_task_handle, mqtt_task_handle, control_task_handle;

static uint64_t now_ms(void) { return (uint64_t)esp_timer_get_time() / 1000; }
static void saturating_increment(uint16_t *value) { if (*value < UINT16_MAX) ++*value; }
static bool parse_status_identity(const mesh_wire_message_t *message, status_identity_t *identity);
static bool receive_status_receipt(const mesh_wire_message_t *message, const uint8_t source[6]);

/* Sequence numbers are shared with registration; gaps are valid. The current
 * episode spans far fewer than 2^31 messages, including across uint32 wrap. */
static bool seq_after(uint32_t next, uint32_t previous) {
    uint32_t distance = next - previous;
    return distance != 0 && distance < UINT32_C(0x80000000);
}

static transport_state_t snapshot(void) {
    transport_state_t copy;
    portENTER_CRITICAL(&state_lock);
    copy = state;
    portEXIT_CRITICAL(&state_lock);
    return copy;
}

static void count_drop(bool rx) {
    portENTER_CRITICAL(&state_lock);
    if (rx) ++state.status.rx_dropped;
    else ++state.status.tx_dropped;
    portEXIT_CRITICAL(&state_lock);
}

static bool is_measurement(const mesh_wire_message_t *m) { return m->kind==MESH_MESSAGE_MEASUREMENT; }
static void publication_drop(const publication_t *p,bool rx) {
    if (is_measurement(&p->message)) ab_runtime_drop();
    else {
        count_drop(rx);
        if(!rx && p->local) {
            portENTER_CRITICAL(&state_lock);
            if(state.session.started && !state.session.is_root && state.session.parent_connected && !state.session.rootless)
                mq_drop(&quality,now_ms());
            portEXIT_CRITICAL(&state_lock);
        }
    }
}
/* Copy under the transport lock, publish after releasing it. A serial rejects
 * snapshots overtaken by another observer; no transport/AB/diag lock nesting. */
static void scan_observe_topology(void) {
    portENTER_CRITICAL(&state_lock);
    if(!state.session.started || !state.session.is_root) root_nonce=0;
    ab_topology_t t={.started=state.session.started,.parent=state.session.parent_connected,
        .root=state.session.is_root,.rootless=state.session.rootless,.voting=state.voting,
        .link_epoch=state.session.epoch,.root_changes=state.status.root_change_count,
        .observation=++scan_observation,.root_nonce=root_nonce};
    memcpy(t.root_mac,state.root_mac,6);
    portEXIT_CRITICAL(&state_lock);
    ab_runtime_topology(&t);
}
static bool scan_config(const mesh_wire_message_t *message) {
    ab_command_t c;
    return ab_command_parse(message->payload,message->length,&c) && !strcmp(c.node_id,message->node_id);
}
static bool checked_root_scan_config(const mesh_wire_message_t *message,uint64_t *nonce) {
    ab_command_t c;
    if(!ab_command_parse(message->payload,message->length,&c) ||
       strcmp(c.node_id,message->node_id) || strcmp(c.root_id,own_id)) return false;
    portENTER_CRITICAL(&state_lock);
    bool valid=root_nonce && c.plan.nonce==root_nonce && state.session.is_root && state.session.mqtt_connected;
    portEXIT_CRITICAL(&state_lock);
    if(valid && nonce) *nonce=c.plan.nonce;
    return valid;
}
static bool root_scan_config(const mesh_wire_message_t *message) { return checked_root_scan_config(message,NULL); }

void mesh_transport_get_status(mesh_transport_status_t *status) {
    if (!status) return;
    portENTER_CRITICAL(&state_lock);
    *status = state.status;
    status->started = state.session.started;
    status->parent_connected = state.session.parent_connected;
    status->is_root = state.session.is_root;
    status->has_ip = state.session.has_ip;
    status->mqtt_connected = state.session.mqtt_connected;
    status->rootless = state.session.rootless;
    if (!status->started || !status->parent_connected) {
        status->layer = -1;
        memset(status->parent_mac, 0, sizeof(status->parent_mac));
    }
    if (status->started && status->parent_connected && !status->rootless)
        memcpy(status->root_mac, status->is_root ? own_mac : state.root_mac, 6);
    else
        memset(status->root_mac, 0, sizeof(status->root_mac));
    portEXIT_CRITICAL(&state_lock);
}

void mesh_transport_drop_pending(void) {
    if (!initialized) return;
    portENTER_CRITICAL(&state_lock);
    ++state.local_generation;
    mq_reset(&quality,now_ms());
    portEXIT_CRITICAL(&state_lock);
    /* A worker may already own a record; its generation check invalidates it.
     * A packet already passed to the SDK can still arrive. Gateway revisions
     * therefore remain authoritative, including after Root changes. */
    /* Registration owns the publisher mutex, so producers cannot refill this
     * bounded queue while it is invalidated. A sender-owned record handles its
     * own generation rejection. Preserve ordinary LR2R1 drop accounting. */
    publication_t discarded;
    for (unsigned i=0;i<LOCAL_DATA_COUNT && xQueueReceive(local_data,&discarded,0)==pdTRUE;i++)
        if(is_measurement(&discarded.message)) ab_runtime_drop();
    xQueueReset(local_data);
    xQueueReset(local_status);
    xQueueReset(local_registration);
}

esp_err_t mesh_transport_publish(mesh_message_kind_t kind, const char *payload, size_t length) {
    bool measurement=kind==MESH_MESSAGE_MEASUREMENT;
    if (!measurement) mesh_diag_request((unsigned)kind);
    if (!initialized) { if(measurement) ab_runtime_drop(); return ESP_ERR_INVALID_STATE; }
    if (kind < MESH_MESSAGE_SEN66 || (kind > MESH_MESSAGE_REGISTRATION_ACK && !measurement) || !payload) {
        if(measurement) ab_runtime_drop();
        return ESP_ERR_INVALID_ARG;
    }
    if (!length || length > (measurement ? AB_JSON_MAX : MESH_TRANSPORT_PAYLOAD_MAX) || memchr(payload, '\0', length)) {
        if(measurement) ab_runtime_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    publication_t record = {0};
    record.message.kind = kind;
    record.message.length = (uint16_t)length;
    memcpy(record.message.node_id, own_id, sizeof(own_id));
    memcpy(record.message.payload, payload, length);
    record.message.payload[length] = '\0';
    record.local = true;
    record.queued_ms = now_ms();
    status_identity_t identity;
    bool status = kind == MESH_MESSAGE_NODE_STATUS && parse_status_identity(&record.message, &identity);
    portENTER_CRITICAL(&state_lock);
    record.epoch = state.session.epoch;
    record.local_generation = state.local_generation;
    if (!measurement) {
    if(state.session.started && !state.session.is_root && state.session.parent_connected && !state.session.rootless && !state.voting)
        mq_request(&quality,record.queued_ms,status,status?identity.seq:0,status?identity.uptime_s*1000:record.queued_ms);
    state.tx_offered = true;
    state.tx_offer_ms = record.queued_ms;
    state.tx_offer_epoch = state.session.epoch;
    state.tx_offer_generation = state.local_generation;
    }
    if (status) {
        delivery_state_t *d = &state.delivery;
        if (!d->offered || d->epoch != state.session.epoch || d->generation != state.local_generation ||
            strcmp(d->boot_id, identity.boot_id)) {
            ++state.delivery_generation;
            memset(sent_proofs, 0, sizeof(sent_proofs));
            proof_extra = 0;
            *d = (delivery_state_t){.offered = true, .epoch = state.session.epoch,
                .generation = state.local_generation, .first_seq = identity.seq,
                .first_uptime_s = identity.uptime_s};
            memcpy(d->boot_id, identity.boot_id, sizeof(d->boot_id));
        }
        d->offer_ms = record.queued_ms;
    }
    portEXIT_CRITICAL(&state_lock);
    if (!measurement) ab_runtime_demand();
    /* The single status slot holds the latest snapshot. Together with the
     * three telemetry slots this preserves the original four-record bound. */
    QueueHandle_t queue = mesh_wire_is_registration(kind) ? local_registration : local_data;
    BaseType_t queued = kind == MESH_MESSAGE_NODE_STATUS ? xQueueOverwrite(local_status, &record) :
                                                        xQueueSend(queue, &record, 0);
    if (queued != pdTRUE) {
        publication_drop(&record,false);
        return ESP_ERR_NO_MEM;
    }
    portENTER_CRITICAL(&state_lock);
    if (!measurement) ++state.status.tx_accepted;
    portEXIT_CRITICAL(&state_lock);
    return ESP_OK;
}

static bool root_ready(const transport_state_t *s) {
    return mesh_session_root_ready(&s->session);
}

static bool child_tx_demand(const transport_state_t *s, uint64_t now) {
    /* An outstanding call is evidence even if all producers have stopped.
     * Otherwise require recent demand belonging to the current link/config. */
    return s->tx_inflight || (s->tx_offered && s->tx_offer_epoch == s->session.epoch &&
           s->tx_offer_generation == s->local_generation && now >= s->tx_offer_ms &&
           now - s->tx_offer_ms <= NATIVE_RECOVERY_TX_DEMAND_MS);
}

static bool child_delivery_demand(const transport_state_t *s, uint64_t now) {
    const delivery_state_t *d = &s->delivery;
    /* The same finite 180 s delivery grace also bounds Root-address discovery.
     * Self-organized IDF 6.0.1 children need not receive ROOT_ADDRESS; a retained
     * current-episode sent identity can identify the Root within 120 s. */
    return d->offered && d->epoch == s->session.epoch && d->generation == s->local_generation &&
           now >= d->offer_ms &&
           now - d->offer_ms <= NATIVE_RECOVERY_TX_DEMAND_MS;
}

/* The netif is never replaced. A role, link or lease change invalidates old
 * MQTT callbacks and queued remote records. Caller holds state_lock. */
static void invalidate_link_locked(void) {
    mesh_session_invalidate(&state.session);
}

/* Caller holds state_lock (or uses an immutable snapshot). */
static bool weak_parent_losses(const transport_state_t *s, uint64_t now) {
    return s->parent_losses == 3 &&
           now - s->parent_loss_ms[0] <= NATIVE_RECOVERY_LOSS_WINDOW_MS;
}

static bool root_address_valid(const uint8_t mac[6]) {
    const uint8_t zero[6] = {0};
    return !(mac[0] & 1U) && memcmp(mac, zero, 6);
}

/* Caller holds state_lock. History is never reused as a current-tree address. */
static void remember_root_locked(const uint8_t mac[6]) {
    if (root_address_valid(mac)) {
        if (root_address_valid(state.last_root_mac) && memcmp(state.last_root_mac, mac, 6)) {
            ++state.status.root_change_count;
            state.inhibit_until = now_ms() + 120000;
        }
        memcpy(state.last_root_mac, mac, 6);
    }
    memcpy(state.root_mac, mac, 6);
}

static void observe_role(bool root) {
    bool changed;
    portENTER_CRITICAL(&state_lock);
    changed = mesh_session_set_role(&state.session, root);
    if (changed) {
        mq_reset(&quality,now_ms());
        state.lease_observation_ready = false;
        memset(state.root_mac, 0, sizeof(state.root_mac));
        state.inhibit_until = now_ms() + 120000;
        state.parent_losses = 0;
        state.recovery_wait_link = false;
        state.qos1_pending = false;
        state.qos1_demand_ms = state.last_puback_ms = 0;
        state.voting = false;
        if (!root && state.vote_wait_demotion && now_ms() <= state.vote_observe_until)
            saturating_increment(&state.status.recovery_demotions);
        state.vote_wait_demotion = false;
    }
    portEXIT_CRITICAL(&state_lock);
    if (changed) {
        /* The Mesh factory disables automatic DHCP. In particular, a former
         * Root must not keep its external DHCP client running as a child. */
        esp_netif_dhcpc_stop(netif_sta);
        ESP_LOGI(TAG, "Role changed: %s", root ? "root" : "node");
    }
}

static void mesh_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    (void)base;
    /* A stopped Mesh has no elected local role. Do not reuse cached Root state. */
    bool root_now = id != MESH_EVENT_STOPPED && esp_mesh_is_root();
    discovery_ble_set_mesh_role(root_now);
    if (id != MESH_EVENT_STOPPED) observe_role(root_now);
    switch (id) {
    case MESH_EVENT_STARTED:
        portENTER_CRITICAL(&state_lock);
        mesh_session_started(&state.session);
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_STOPPED:
        portENTER_CRITICAL(&state_lock);
        mesh_session_stopped(&state.session);
        state.lease_observation_ready = false;
        state.voting = false;
        state.parent_losses = 0;
        state.recovery_wait_link = false;
        state.qos1_pending = false;
        state.qos1_demand_ms = state.last_puback_ms = 0;
        memset(state.root_mac, 0, sizeof(state.root_mac));
        portEXIT_CRITICAL(&state_lock);
        esp_netif_dhcpc_stop(netif_sta);
        break;
    case MESH_EVENT_PARENT_CONNECTED: {
        const mesh_event_connected_t *event = data;
        bool root = esp_mesh_is_root();
        if ((event->self_layer == MESH_ROOT_LAYER) != root) break;
        portENTER_CRITICAL(&state_lock);
        if (memcmp(state.loss_bssid, event->connected.bssid, 6))
            state.parent_losses = 0;
        memcpy(state.loss_bssid, event->connected.bssid, 6);
        ++state.parent_connection;
        mesh_session_parent_connected(&state.session, root);
        state.lease_observation_ready = false;
        memset(state.root_mac, 0, sizeof(state.root_mac));
        uint32_t epoch = state.session.epoch;
        state.status.layer = event->self_layer;
        memcpy(state.status.parent_mac, event->connected.bssid, 6);
        portEXIT_CRITICAL(&state_lock);
        esp_err_t error = esp_netif_dhcpc_stop(netif_sta);
        if (root) {
            /* Do not let the bridge observe the previous parent's lease in
             * the interval between updating the session and resetting DHCP. */
            if (error == ESP_OK || error == ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED)
                error = esp_netif_dhcpc_start(netif_sta);
            if (error != ESP_OK) ESP_LOGW(TAG, "Root DHCP start: %s", esp_err_to_name(error));
            portENTER_CRITICAL(&state_lock);
            if (state.session.epoch == epoch)
                state.lease_observation_ready = error == ESP_OK;
            portEXIT_CRITICAL(&state_lock);
        }
        ESP_LOGI(TAG, "Parent connected, layer=%d root=%d", event->self_layer, root);
        break;
    }
    case MESH_EVENT_PARENT_DISCONNECTED: {
        const mesh_event_disconnected_t *event = data;
        portENTER_CRITICAL(&state_lock);
        ++state.status.parent_disconnect_count;
        /* Count only actual associated-link losses with usable weak RSSI.
         * Duplicate/other-BSSID events and unknown RSSI add no evidence.
         * Rootless does not change the identity of an associated parent. */
        if (event && state.session.parent_connected &&
            !memcmp(event->bssid, state.status.parent_mac, 6) &&
            event->rssi >= -120 && event->rssi <= -80) {
            if (state.parent_losses == 3) {
                state.parent_loss_ms[0] = state.parent_loss_ms[1];
                state.parent_loss_ms[1] = state.parent_loss_ms[2];
            } else ++state.parent_losses;
            state.parent_loss_ms[state.parent_losses - 1] = now_ms();
        }
        state.status.last_disconnect_valid = event != NULL;
        if (event) {
            state.status.last_disconnect_reason = event->reason;
            state.status.last_disconnect_rssi = event->rssi;
        }
        mesh_session_parent_disconnected(&state.session);
        state.lease_observation_ready = false;
        memset(state.root_mac, 0, sizeof(state.root_mac));
        state.status.layer = -1;
        memset(state.status.parent_mac, 0, 6);
        portEXIT_CRITICAL(&state_lock);
        if (event) mesh_diag_disconnected(event->bssid, event->reason, event->rssi);
        esp_netif_dhcpc_stop(netif_sta);
        break;
    }
    case MESH_EVENT_LAYER_CHANGE:
        portENTER_CRITICAL(&state_lock);
        state.status.layer = ((mesh_event_layer_change_t *)data)->new_layer;
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_ROOT_ADDRESS: {
        const mesh_event_root_address_t *root = data;
        portENTER_CRITICAL(&state_lock);
        if (memcmp(state.root_mac, root->addr, 6)) {
            if (!state.session.is_root) {
                ++state.session.epoch;
                state.session.gateway_reachable = false;
            }
        }
        if (state.session.started && state.session.parent_connected) {
            remember_root_locked(root->addr);
        }
        portEXIT_CRITICAL(&state_lock);
        break;
    }
    case MESH_EVENT_NETWORK_STATE: {
        bool rootless = ((mesh_event_network_state_t *)data)->is_rootless;
        portENTER_CRITICAL(&state_lock);
        if (rootless && !state.session.rootless) {
            memset(state.root_mac, 0, sizeof(state.root_mac));
        }
        mesh_session_set_rootless(&state.session, rootless);
        portEXIT_CRITICAL(&state_lock);
        break;
    }
    case MESH_EVENT_TODS_STATE:
        portENTER_CRITICAL(&state_lock);
        state.session.gateway_reachable = *(mesh_event_toDS_state_t *)data == MESH_TODS_REACHABLE;
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_VOTE_STARTED:
    case MESH_EVENT_VOTE_STOPPED:
    case MESH_EVENT_ROOT_SWITCH_REQ:
    case MESH_EVENT_ROOT_SWITCH_ACK:
        portENTER_CRITICAL(&state_lock);
        if (id == MESH_EVENT_VOTE_STARTED && !state.voting) {
            state.voting = true;
            if (!state.session.is_root) {
                ++state.session.epoch;
                memset(state.root_mac, 0, 6);
                state.session.gateway_reachable = false;
            }
            state.vote_hold_until = now_ms() + VOTE_HOLD_MAX_MS;
        }
        if ((id == MESH_EVENT_VOTE_STOPPED && state.voting) || id == MESH_EVENT_ROOT_SWITCH_REQ ||
            id == MESH_EVENT_ROOT_SWITCH_ACK) {
            if (!state.session.is_root) {
                if (id != MESH_EVENT_VOTE_STOPPED) ++state.session.epoch;
                state.delivery.offered = false;
                memset(state.root_mac, 0, 6);
                state.session.gateway_reachable = false;
            }
            if (id == MESH_EVENT_VOTE_STOPPED) state.voting = false;
        }
        state.inhibit_until = now_ms() + 120000;
        if (state.voting && state.inhibit_until > state.vote_hold_until)
            state.inhibit_until = state.vote_hold_until;
        portEXIT_CRITICAL(&state_lock);
        break;
    default:
        break;
    }
    scan_observe_topology();
    mesh_diag_mesh_event(id);
}

typedef struct {
    uint32_t epoch;
    const esp_netif_ip_info_t *event_ip; /* NULL when reconciling without GOT_IP. */
} lease_observation_t;

/* Execute the live read AND its commit in TCP/IP context. DHCP cannot expire
 * or replace the lease between them. No state lock is held across SDK calls.
 * DHCP_STARTED alone is not a lease: it also covers selecting/requesting. */
static esp_err_t observe_lease_tcpip(void *arg) {
    const lease_observation_t *observation = arg;
    esp_netif_ip_info_t current = {0};
    esp_netif_dhcp_status_t dhcp_status;
    struct netif *netif = esp_netif_get_netif_impl(netif_sta);
    bool valid = netif && esp_netif_is_netif_up(netif_sta) && netif_is_link_up(netif) &&
                 esp_netif_dhcpc_get_status(netif_sta, &dhcp_status) == ESP_OK &&
                 dhcp_status == ESP_NETIF_DHCP_STARTED && dhcp_supplied_address(netif) &&
                 esp_netif_get_ip_info(netif_sta, &current) == ESP_OK && current.ip.addr;
    if (observation->event_ip &&
        (!valid || current.ip.addr != observation->event_ip->ip.addr ||
         current.netmask.addr != observation->event_ip->netmask.addr ||
         current.gw.addr != observation->event_ip->gw.addr)) return ESP_OK;
    bool root = esp_mesh_is_root();
    portENTER_CRITICAL(&state_lock);
    if (root && state.lease_observation_ready && state.session.epoch == observation->epoch &&
        state.session.started && state.session.is_root && state.session.parent_connected) {
        if (valid) mesh_session_got_ip(&state.session, observation->epoch);
        else if (state.session.has_ip) invalidate_link_locked();
    }
    portEXIT_CRITICAL(&state_lock);
    return ESP_OK;
}

static void reconcile_root_lease(const esp_netif_ip_info_t *event_ip) {
    transport_state_t before = snapshot();
    if (!before.lease_observation_ready || !before.session.started ||
        !before.session.is_root || !before.session.parent_connected || !esp_mesh_is_root()) return;
    lease_observation_t observation = {.epoch = before.session.epoch, .event_ip = event_ip};
    esp_netif_tcpip_exec(observe_lease_tcpip, &observation);
}

static void ip_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    (void)base;
    if (id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        if (event && event->esp_netif == netif_sta) reconcile_root_lease(&event->ip_info);
    } else if (id == IP_EVENT_STA_LOST_IP) {
        /* Consult the current lease, so a delayed LOST_IP cannot revoke a new one. */
        reconcile_root_lease(NULL);
    }
}

static bool recovery_valid(const recovery_request_t *request) {
    bool root = esp_mesh_is_root();
    transport_state_t s = snapshot();
    if (s.session.epoch != request->epoch || !s.session.started || s.voting ||
        now_ms() < s.inhibit_until || root != s.session.is_root) return false;
    switch (request->action) {
    case REC_ROOT_VOTE:
        return s.session.is_root && s.session.parent_connected && !s.session.rootless &&
               weak_parent_losses(&s, now_ms());
    case REC_CHILD_RESELECT:
        if (request->tx_stall || request->delivery_stall)
            return !s.session.is_root && s.session.parent_connected && !s.session.rootless &&
                   s.local_generation == request->local_generation &&
                   ((request->tx_stall && s.tx_progress == request->tx_progress && child_tx_demand(&s, now_ms())) ||
                    (request->delivery_stall && s.delivery.progress == request->delivery_progress &&
                     child_delivery_demand(&s, now_ms())));
        return !s.session.is_root && (request->repeated_loss ?
               s.session.parent_connected && !s.session.rootless && weak_parent_losses(&s, now_ms()) :
               !s.session.parent_connected || s.session.rootless);
    case REC_CHILD_REPAIR: {
        /* Selection already validated quality before gating the sender. In
         * QUIET, aging caused by that gate must not cancel its own request.
         * Natural reconnection/role/config/vote changes still invalidate it. */
        if(request==&repair.request && repair.phase==REPAIR_QUIET)
            return request->quality_bad && !root && !s.session.is_root &&
                s.session.parent_connected && !s.session.rootless &&
                s.local_generation==request->local_generation &&
                s.parent_connection==request->parent_connection &&
                s.status.root_change_count==repair.root_changes;
        portENTER_CRITICAL(&state_lock);
        mesh_quality_view_t q=mq_evaluate(&quality,now_ms(),state.session.started && !state.session.is_root &&
            state.session.parent_connected && !state.session.rootless && !state.voting);
        portEXIT_CRITICAL(&state_lock);
        return !root && !s.session.is_root && s.session.parent_connected && !s.session.rootless &&
            s.local_generation==request->local_generation && s.parent_connection==request->parent_connection && q.bad;
    }
    case REC_ROUTER_RECONNECT:
        return s.session.is_root && !s.session.parent_connected;
    case REC_DHCP_START:
        return s.session.is_root && s.session.parent_connected &&
               !s.session.rootless && !s.session.has_ip;
    case REC_MQTT_RECONNECT:
    case REC_MQTT_RECREATE:
        return root_ready(&s) && s.mqtt_generation == request->mqtt_generation &&
               s.last_puback_ms == request->progress_ms &&
               (!s.session.mqtt_connected || s.qos1_pending) &&
               (request->action != REC_MQTT_RECONNECT || s.session.mqtt_connected);
    default:
        return false;
    }
}

static void recovery_finished(const recovery_request_t *request, esp_err_t result) {
    portENTER_CRITICAL(&state_lock);
    state.status.recovery_error = result;
    if (result == ESP_OK) {
        saturating_increment(&state.status.recovery_ok);
        if (request->action == REC_CHILD_RESELECT || request->action == REC_ROUTER_RECONNECT || request->action == REC_CHILD_REPAIR) {
            state.recovery_wait_link = true;
            state.recovery_link_generation = request->parent_connection;
            state.recovery_link_root = request->action == REC_ROUTER_RECONNECT;
            state.recovery_link_until = now_ms() + 300000;
            memcpy(state.recovery_parent_bssid, request->parent_bssid, 6);
        }
        if (request->action == REC_ROOT_VOTE) {
            saturating_increment(&state.status.recovery_votes);
            state.vote_wait_demotion = true;
            state.vote_observe_until = now_ms() + 300000;
        }
    }
    portEXIT_CRITICAL(&state_lock);
    mesh_diag_recovery(request->action,
        (request->tx_stall ? DIAG_REC_TX : 0) | (request->delivery_stall ? DIAG_REC_DELIVERY : 0) |
        (request->repeated_loss ? DIAG_REC_REPEATED : 0), true, result);
    ESP_LOGI(TAG, "Recovery action=%d epoch=%" PRIu32 " API=%s (%d)",
             request->action, request->epoch, esp_err_to_name(result), result);
}

/* Consume evidence before an API can cause callbacks. Do not erase losses
 * from a later link/role generation after a blocking SDK operation returns. */
static bool consume_parent_losses(const recovery_request_t *request) {
    portENTER_CRITICAL(&state_lock);
    bool current = state.session.epoch == request->epoch;
    if (current) state.parent_losses = 0;
    portEXIT_CRITICAL(&state_lock);
    return current;
}

static esp_err_t start_missing_dhcp_tcpip(void *arg) {
    const recovery_request_t *request = arg;
    if (!recovery_valid(request)) return ESP_ERR_INVALID_STATE;
    struct netif *netif = esp_netif_get_netif_impl(netif_sta);
    esp_netif_dhcp_status_t dhcp_status;
    /* An already running DHCP client retries by itself. In particular do not
     * stop a selecting/renewing client, clear a live lease or override P0. */
    if (!netif || !esp_netif_is_netif_up(netif_sta) || !netif_is_link_up(netif) ||
        dhcp_supplied_address(netif) ||
        esp_netif_dhcpc_get_status(netif_sta, &dhcp_status) != ESP_OK ||
        dhcp_status != ESP_NETIF_DHCP_STOPPED || !recovery_valid(request))
        return ESP_ERR_INVALID_STATE;
    esp_err_t result = esp_netif_dhcpc_start(netif_sta);
    bool still_root = esp_mesh_is_root();
    portENTER_CRITICAL(&state_lock);
    if (state.session.epoch == request->epoch && state.session.is_root &&
        state.session.parent_connected && still_root)
        state.lease_observation_ready = result == ESP_OK;
    portEXIT_CRITICAL(&state_lock);
    return result;
}

/* The existing MQTT owner executes ALL recovery. In particular, Mesh/Wi-Fi
 * work must not block the default event loop that delivers its SDK events.
 * P1 callbacks record state; P0 DHCP/lease handling is retained. Revalidate
 * after any preceding SDK operation. */
static void recover_mesh(const recovery_request_t *request) {
    esp_err_t result = ESP_ERR_INVALID_STATE;
    if (!request) return;
    /* Retain BLE-AB1 TX_STALL escape from a stuck SDK call. Other legacy
     * requests still wait for an idle sender; never overlap a REPAIR gate. */
    bool gated=false;
    if (recovery_valid(request)) {
        switch (request->action) {
        case REC_ROOT_VOTE:
            if (!consume_parent_losses(request)) break;
            result = esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED);
            break;
        case REC_CHILD_RESELECT:
            if (request->tx_stall || request->delivery_stall) {
                ESP_LOGW(TAG, "Child uplink stalled: local=%d receipt=%d; reselecting parent",
                         request->tx_stall, request->delivery_stall);
            }
            /* UART logging may block and run event callbacks. Nothing that can
             * wait is permitted between this last check and the SDK call. */
            if (!recovery_valid(request)) break;
            portENTER_CRITICAL(&state_lock);
            gated=(!sdk_busy || request->tx_stall) && !recovery_gate;
            if(gated) recovery_gate=true;
            portEXIT_CRITICAL(&state_lock);
            if(!gated) { mesh_probe_recovery(PROBE_DEFERRED,ESP_ERR_INVALID_STATE,0); break; }
            if (!recovery_valid(request)) break;
            if (!consume_parent_losses(request)) break;
            result = esp_mesh_set_self_organized(true, true);
            break;
        case REC_ROUTER_RECONNECT:
            result = esp_mesh_set_self_organized(true, false);
            if (result == ESP_OK) {
                result = ESP_ERR_INVALID_STATE;
                if (recovery_valid(request)) result = esp_mesh_connect();
            }
            break;
        case REC_DHCP_START:
            result = esp_netif_tcpip_exec(start_missing_dhcp_tcpip, (void *)request);
            break;
        default:
            break;
        }
    }
    if(gated) { portENTER_CRITICAL(&state_lock); recovery_gate=false; portEXIT_CRITICAL(&state_lock); }
    recovery_finished(request, result);
}

static void repair_release(void) {
    portENTER_CRITICAL(&state_lock); recovery_gate=false; portEXIT_CRITICAL(&state_lock);
}
static void repair_end(unsigned phase,int32_t result) {
    mesh_probe_recovery(phase,result,repair.subtree_nodes);
    if(!repair.accepted) recovery_finished(&repair.request,result);
    repair_release(); repair.phase=REPAIR_IDLE;
}
static void repair_restore(int32_t result,bool connect_needed) {
    repair.result=result; repair.connect_needed=connect_needed;
    repair.phase=REPAIR_RESTORE; repair.restores=0; repair.retry_after=0;
}
static void repair_start(const recovery_request_t *request) {
    if(repair.phase!=REPAIR_IDLE || !recovery_valid(request)) {
        recovery_finished(request,ESP_ERR_INVALID_STATE); return;
    }
    memset(&repair,0,sizeof(repair)); repair.request=*request;
    repair.phase=REPAIR_QUIET; repair.deadline=now_ms()+30000;
    portENTER_CRITICAL(&state_lock);
    repair.disconnects=state.status.parent_disconnect_count;
    repair.connection=state.parent_connection;
    repair.root_changes=state.status.root_change_count;
    recovery_gate=true; /* existing SDK call finishes; no next dispatch */
    portEXIT_CRITICAL(&state_lock);
}
static bool repair_tick(const mesh_quality_view_t *q) {
    if(repair.phase==REPAIR_IDLE) return false;
    uint64_t now=now_ms(); transport_state_t s=snapshot();
    bool root=esp_mesh_is_root();
    bool invalid=!s.session.started || root || s.session.is_root || s.voting ||
        s.local_generation!=repair.request.local_generation || s.status.root_change_count!=repair.root_changes;
    if(repair.phase==REPAIR_LATCHED) return true; /* bounded restore failed: manual review, BLE/sensors continue */
    if(repair.phase==REPAIR_QUIET) {
        if(invalid || !recovery_valid(&repair.request)) { repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE); return true; }
        portENTER_CRITICAL(&state_lock); bool busy=sdk_busy; portEXIT_CRITICAL(&state_lock);
        if(busy) {
            if(now>=repair.deadline) repair_end(PROBE_DEFERRED,ESP_ERR_TIMEOUT);
            return true;
        }
        if(!esp_mesh_get_self_organized()) { repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE); return true; }
        int subtree=esp_mesh_get_routing_table_size();
        repair.subtree_nodes=subtree>0?(unsigned)subtree:0;
        mesh_probe_recovery(PROBE_BEGIN,0,repair.subtree_nodes);
        if(!recovery_valid(&repair.request)) { repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE); return true; }
        /* Freeze automatic selection for the bounded cut/restore transaction.
         * Do not fix the Root, change a parent, or call direct Wi-Fi APIs. */
        repair.self_disabled=true; /* restore even if disable reports failure */
        int32_t result=esp_mesh_set_self_organized(false,false);
        if(result!=ESP_OK || !recovery_valid(&repair.request)) {
            repair_restore(result!=ESP_OK?result:ESP_ERR_INVALID_STATE,false); return true;
        }
        /* No logging, heap getter or other blocking work after this guard. */
        if(esp_mesh_is_root()) { repair_restore(ESP_ERR_INVALID_STATE,false); return true; }
        result=esp_mesh_disconnect();
        mesh_probe_recovery(PROBE_API,result,repair.subtree_nodes);
        if(result!=ESP_OK) { repair_restore(result,false); return true; }
        repair.phase=REPAIR_DISCONNECT; repair.deadline=now_ms()+10000;
        return true;
    }
    if(invalid && repair.phase!=REPAIR_RESTORE) {
        if(repair.self_disabled) repair_restore(ESP_ERR_INVALID_STATE,false);
        else repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE);
        return true;
    }
    if(repair.phase==REPAIR_DISCONNECT) {
        if(s.status.parent_disconnect_count!=repair.disconnects) {
            mesh_probe_recovery(PROBE_DISCONNECTED,0,repair.subtree_nodes);
            repair_restore(ESP_OK,true);
        } else if(s.parent_connection!=repair.connection) repair_restore(ESP_ERR_INVALID_STATE,false);
        else if(now>=repair.deadline) repair_restore(ESP_ERR_TIMEOUT,true);
        return true;
    }
    if(repair.phase==REPAIR_RESTORE) {
        if(now<repair.retry_after) return true;
        /* Always restore the previously enabled policy, including after Root
         * promotion. select_parent=false never intentionally demotes Root. */
        int32_t result=esp_mesh_set_self_organized(true,false);
        ++repair.restores;
        if(result!=ESP_OK || !esp_mesh_get_self_organized()) {
            if(repair.restores<3) { repair.retry_after=now+1000; return true; }
            mesh_probe_recovery(PROBE_RESTORE_FAILED,result!=ESP_OK?result:ESP_FAIL,repair.subtree_nodes);
            recovery_finished(&repair.request,result!=ESP_OK?result:ESP_FAIL);
            repair_release(); repair.phase=REPAIR_LATCHED; return true;
        }
        repair.self_disabled=false;
        s=snapshot(); root=esp_mesh_is_root();
        if(!s.session.started || root || s.session.is_root || s.voting ||
           s.local_generation!=repair.request.local_generation || s.status.root_change_count!=repair.root_changes) {
            repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE); return true;
        }
        if(repair.connect_needed && !s.session.parent_connected) {
            if(esp_mesh_is_root()) { repair_end(PROBE_CANCELLED,ESP_ERR_INVALID_STATE); return true; }
            result=esp_mesh_connect(); /* at most one connect request per attempt */
            if(result!=ESP_OK) { repair_end(PROBE_UNRECOVERED,result); return true; }
        }
        if(repair.result!=ESP_OK) { repair_end(PROBE_UNRECOVERED,repair.result); return true; }
        recovery_finished(&repair.request,ESP_OK); repair.accepted=true;
        repair_release(); repair.phase=REPAIR_LINK; repair.deadline=now_ms()+120000;
        return true;
    }
    if(repair.phase==REPAIR_LINK) {
        if(s.session.parent_connected && !s.session.rootless && s.parent_connection!=repair.connection) {
            mesh_probe_recovery(PROBE_LINKED,0,repair.subtree_nodes);
            repair.connection=s.parent_connection;
            portENTER_CRITICAL(&state_lock); mq_reset(&quality,now); portEXIT_CRITICAL(&state_lock);
            repair.phase=REPAIR_VERIFY; repair.deadline=now+360000; repair.healthy_since=0;
        } else if(now>=repair.deadline) repair_end(PROBE_UNRECOVERED,ESP_ERR_TIMEOUT);
        return true;
    }
    if(repair.phase==REPAIR_VERIFY) {
        if(!s.session.parent_connected || s.session.rootless || s.parent_connection!=repair.connection) {
            repair_end(PROBE_UNRECOVERED,ESP_ERR_INVALID_STATE); return true;
        }
        if(q->healthy) {
            if(!repair.healthy_since) repair.healthy_since=now;
            if(now-repair.healthy_since>=60000) { repair_end(PROBE_RECOVERED,ESP_OK); return true; }
        } else repair.healthy_since=0;
        if(now>=repair.deadline) repair_end(PROBE_UNRECOVERED,ESP_ERR_TIMEOUT);
        return true;
    }
    return true;
}

static bool enqueue_root(publication_t *record, const transport_state_t *view) {
    record->epoch = view->session.epoch;
    QueueHandle_t queue = mesh_wire_is_registration(record->message.kind) ? root_registration :
                          record->message.kind == MESH_MESSAGE_NODE_STATUS ? root_status : root_data;
    return xQueueSend(queue, record, 0) == pdTRUE;
}

static void mesh_receiver(void *arg) {
    (void)arg;
    static uint8_t buffer[MESH_MTU];
    static publication_t record;
    static control_t command;
    for (;;) {
        if (!snapshot().session.started) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        mesh_addr_t from = {0};
        mesh_data_t data = {.data = buffer, .size = sizeof(buffer)};
        int flags = 0;
        esp_err_t error = esp_mesh_recv(&from, &data, 500, &flags, NULL, 0);
        portENTER_CRITICAL(&state_lock);
        if (error == ESP_OK) ++state.status.mesh_rx_ok;
        else if (error != ESP_ERR_MESH_TIMEOUT) ++state.status.mesh_rx_errors;
        portEXIT_CRITICAL(&state_lock);
        if (error != ESP_OK) {
            if (error != ESP_ERR_MESH_TIMEOUT) vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (data.proto != MESH_PROTO_BIN || !mesh_wire_decode(buffer, data.size, &record.message)) {
#if OMK_DIAGNOSTICS
            if (snapshot().session.is_root) mesh_diag_root(NULL, DIAG_ROOT_WIRE_BAD);
#endif
            count_drop(true);
            continue;
        }
        transport_state_t view = snapshot();
        record.queued_ms = now_ms();
        record.local = false;
        record.local_generation = 0;
        memcpy(record.source, from.addr, 6);
        if (view.session.is_root && (record.message.kind == MESH_MESSAGE_SEN66 ||
                                    record.message.kind == MESH_MESSAGE_NODE_STATUS))
            mesh_diag_root(&record.message, DIAG_ROOT_RX);
        if (record.message.kind == MESH_MESSAGE_STATUS_RECEIPT) {
            if (!receive_status_receipt(&record.message, from.addr)) count_drop(true);
        } else if (record.message.kind == MESH_MESSAGE_CONFIG) {
            if (view.session.is_root || !view.session.parent_connected || view.session.rootless ||
                strcmp(record.message.node_id, own_id) || memcmp(from.addr, view.root_mac, 6)) {
                count_drop(true);
                continue;
            }
            command.scan = scan_config(&record.message);
            command.message = record.message;
            command.queued_ms = record.queued_ms;
            command.epoch = view.session.epoch;
            memcpy(command.peer, from.addr, 6);
            if (xQueueSend(apply_configs, &command, 0) != pdTRUE) count_drop(true);
        } else if (!mesh_session_accept_ingress(&view.session) || !esp_mesh_is_root() || !enqueue_root(&record, &view)) {
            if (!is_measurement(&record.message)) mesh_diag_root(&record.message, DIAG_ROOT_INGRESS_REJECT);
            publication_drop(&record,true);
        }
    }
}

static esp_err_t send_message(const mesh_wire_message_t *message, const uint8_t *destination,
                              const publication_t *origin) {
    /* Only mesh_sender calls this function: esp_mesh_send is non-reentrant. */
    static uint8_t buffer[MESH_WIRE_MAX_SIZE];
    size_t length;
    bool measurement = is_measurement(message);
    /* Sender-owned pacing: even after the five-second respite expires, only
     * ordinary traffic may probe/release active suppression. No SDK accounting. */
    if(measurement && tx_pacing.active) { ab_runtime_drop(); return ESP_ERR_INVALID_STATE; }
    bool receipt = message->kind == MESH_MESSAGE_STATUS_RECEIPT;
    bool downlink = receipt || message->kind == MESH_MESSAGE_CONFIG;
    uint64_t command_nonce=0;
    if (message->kind==MESH_MESSAGE_CONFIG && scan_config(message) &&
        !checked_root_scan_config(message,&command_nonce)) return ESP_ERR_INVALID_STATE;
    if (downlink && !destination) return ESP_ERR_INVALID_ARG;
    if (!destination && !origin) { if(measurement) ab_runtime_drop(); return ESP_ERR_INVALID_ARG; }
    if (!mesh_wire_encode(message, buffer, sizeof(buffer), &length)) { if(measurement) ab_runtime_drop(); return ESP_ERR_INVALID_ARG; }
    mesh_data_t data = {
        .data = buffer, .size = (uint16_t)length,
        /* Gateway durably replays config every 5 s until matching endpoint
         * state/ACK, not just PUBACK. Duplicates do not rewrite Node NVS.
         * Both downlinks can therefore be best effort without pinning this
         * sole sender. Upstream reliability remains SDK P2P. */
        .proto = MESH_PROTO_BIN, .tos = downlink ? MESH_TOS_DEF : MESH_TOS_P2P,
    };
    mesh_addr_t to;
    if (destination) memcpy(to.addr, destination, sizeof(to.addr));
    /* NULL addresses the current Root application. Like the IDF example this
     * uses its ordinary recv queue, not external IPv4:PORT / recv_toDS. */
    uint32_t epoch, generation;
    status_identity_t identity;
    bool status = !destination && message->kind == MESH_MESSAGE_NODE_STATUS &&
                  parse_status_identity(message, &identity);
    portENTER_CRITICAL(&state_lock);
    epoch = origin ? origin->epoch : state.session.epoch;
    generation = origin ? origin->local_generation : state.local_generation;
    if (!destination && (epoch != state.session.epoch || generation != state.local_generation ||
        !state.session.started || state.session.is_root || !state.session.parent_connected || state.session.rootless)) {
        portEXIT_CRITICAL(&state_lock);
        if (measurement) ab_runtime_drop();
        return ESP_ERR_INVALID_STATE;
    }
    if(recovery_gate) {
        portEXIT_CRITICAL(&state_lock);
        if(measurement) ab_runtime_drop();
        return ESP_ERR_INVALID_STATE;
    }
    sdk_busy=true;
    if (!destination && !measurement) state.tx_inflight = true;
    delivery_state_t *d = &state.delivery;
    if (status && d->offered && d->epoch == epoch && d->generation == generation &&
        !strcmp(d->boot_id, identity.boot_id) &&
        (identity.seq == d->first_seq || seq_after(identity.seq, d->first_seq)) &&
        (!d->submitted || seq_after(identity.seq, d->last_sent_seq))) {
        /* Register before entering the SDK: a receipt can arrive before return.
         * A later SDK error must not erase an already validated receipt. */
        d->submitted = true;
        d->last_sent_seq = identity.seq;
        d->last_sent_uptime_s = identity.uptime_s;
        if (!state.voting) {
            uint64_t bucket = now_ms() / 5000;
            unsigned slot = bucket % PROOF_BUCKETS;
            if (sent_proofs[slot].used && sent_proofs[slot].bucket == bucket)
                slot = PROOF_BUCKETS + (proof_extra++ % PROOF_EXTRA);
            sent_proofs[slot] = (sent_proof_t){.used = true, .seq = identity.seq,
                .uptime_s = identity.uptime_s, .bucket = bucket};
        }
    }
    portEXIT_CRITICAL(&state_lock);
    scan_observe_topology();
    if(command_nonce) {
        /* Last dispatch guard after encoding/topology publication, before any
         * SDK accounting. A reconnect can invalidate an already-popped queue
         * entry. Beyond this handoff boundary SDK packets cannot be recalled. */
        portENTER_CRITICAL(&state_lock);
        bool current=command_nonce==root_nonce && state.session.is_root && state.session.mqtt_connected;
        portEXIT_CRITICAL(&state_lock);
        if(!current) {
            portENTER_CRITICAL(&state_lock); sdk_busy=false; portEXIT_CRITICAL(&state_lock);
            return ESP_ERR_INVALID_STATE;
        }
    }
    if(!destination && !measurement) mesh_probe_begin((uint8_t)message->kind,epoch);
    mesh_diag_tx_begin(message, destination == NULL);
    ab_runtime_sdk_begin((uint8_t)message->kind,destination==NULL);
    esp_err_t error = esp_mesh_send(destination ? &to : NULL, &data,
        (destination ? MESH_DATA_P2P : MESH_DATA_TODS) | (downlink ? MESH_DATA_NONBLOCK : 0), NULL, 0);
    ab_runtime_sdk_end(error);
    mesh_diag_tx_end(error);
    portENTER_CRITICAL(&state_lock);
    uint32_t completed_epoch=state.session.epoch;
    if(!destination && !measurement && completed_epoch==epoch && state.local_generation==generation &&
       state.session.started && !state.session.is_root && state.session.parent_connected && !state.session.rootless)
        mq_sdk(&quality,now_ms(),error);
    portEXIT_CRITICAL(&state_lock);
    if(!destination && !measurement) mesh_probe_end(error,completed_epoch);
    if (!destination && !measurement) mesh_tx_pacing_result(&tx_pacing, now_ms(), error);
    portENTER_CRITICAL(&state_lock);
    if (!measurement) {
        if (error == ESP_OK) ++state.status.mesh_tx_ok;
        else ++state.status.mesh_tx_errors;
    }
    if (!destination && !measurement) {
        state.tx_inflight = false;
        /* A late completion from an old link/config must not cancel recovery
         * of the new one. This records local SDK progress, not Root receipt. */
        if (error == ESP_OK && state.session.epoch == epoch && state.local_generation == generation &&
            state.session.started && !state.session.is_root && state.session.parent_connected &&
            !state.session.rootless) ++state.tx_progress;
    }
    portEXIT_CRITICAL(&state_lock);
    portENTER_CRITICAL(&state_lock); sdk_busy=false; portEXIT_CRITICAL(&state_lock);
    return error;
}

/* Round robin among classes prevents registration/status/telemetry starvation.
 * Each queue remains bounded; no producer or SDK call runs under a new lock. */
static bool next_publication(QueueHandle_t registration, QueueHandle_t status, QueueHandle_t data,
                             unsigned *turn, publication_t *record) {
    QueueHandle_t queues[] = {registration, status, data};
    for (unsigned i = 0; i < 3; ++i) {
        unsigned selected = (*turn + i) % 3;
        if (xQueueReceive(queues[selected], record, 0) == pdTRUE) {
            *turn = (selected + 1) % 3;
            return true;
        }
    }
    return false;
}

static bool upstream_paused(void) {
    uint64_t now = now_ms();
    /* Read only the pacing context instead of adding another whole transport
     * snapshot to the sender's stack. No SDK or blocking work under this lock. */
    portENTER_CRITICAL(&state_lock);
    bool wait = mesh_tx_pacing_wait(&tx_pacing, now,
        state.session.started && !state.session.is_root &&
        state.session.parent_connected && !state.session.rootless);
    portEXIT_CRITICAL(&state_lock);
    return wait;
}

static void mesh_sender(void *arg) {
    (void)arg;
    static publication_t record;
    static control_t command;
    static unsigned publication_turn;
    status_receipt_t receipt;
    for (;;) {
        portENTER_CRITICAL(&state_lock); bool gated=recovery_gate; portEXIT_CRITICAL(&state_lock);
        if(gated) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
        if (xQueueReceive(mesh_configs, &command, 0) == pdTRUE) {
            transport_state_t view = snapshot();
            if (!root_ready(&view) || command.epoch != view.session.epoch || !esp_mesh_is_root() ||
                !mesh_wire_is_fresh(command.queued_ms, now_ms()) ||
                (command.scan && !root_scan_config(&command.message)) ||
                send_message(&command.message, command.peer, NULL) != ESP_OK) count_drop(false);
        }
        /* One best-effort receipt per pass, interleaved with existing work.
         * Compact queue entries avoid another queue of 1 KiB payloads. */
        if (xQueueReceive(status_receipts, &receipt, 0) == pdTRUE) {
            transport_state_t view = snapshot();
            if (mesh_session_accept_ingress(&view.session) && !view.session.rootless &&
                receipt.epoch == view.session.epoch && esp_mesh_is_root() &&
                mesh_wire_is_fresh(receipt.queued_ms, now_ms())) {
                command.message.kind = MESH_MESSAGE_STATUS_RECEIPT;
                memcpy(command.message.node_id, receipt.node_id, sizeof(receipt.node_id));
                int length = snprintf(command.message.payload, sizeof(command.message.payload),
                    "{\"boot_id\":\"%s\",\"seq\":%" PRIu32 ",\"uptime_s\":%" PRIu64 "}",
                    receipt.identity.boot_id, receipt.identity.seq, receipt.identity.uptime_s);
                command.message.length = (uint16_t)length;
                if (length <= 0 || length >= (int)sizeof(command.message.payload) ||
                    send_message(&command.message, receipt.peer, NULL) != ESP_OK) count_drop(false);
            } else count_drop(false);
        }
        if (upstream_paused()) {
            /* Keep only the existing bounded queues; no failed packet is requeued.
             * Recovery runs in its existing independent owner task. */
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (!next_publication(local_registration, local_status, local_data, &publication_turn, &record)) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        transport_state_t view = snapshot();
        if (!view.session.started || record.epoch != view.session.epoch || record.local_generation != view.local_generation ||
            !mesh_wire_is_fresh(record.queued_ms, now_ms())) {
            publication_drop(&record,false);
            continue;
        }
        if (view.session.is_root && esp_mesh_is_root()) {
            memcpy(record.source, own_mac, 6);
            if (!enqueue_root(&record, &view)) publication_drop(&record,false);
        } else {
            /* toDS is advisory. A same-Root parent reconnect need not produce
             * a new toDS transition; it must not leave telemetry gated forever.
             * Root drains and drops immediately when MQTT is unavailable. */
            if (!view.session.parent_connected || view.session.rootless) publication_drop(&record,false);
            else {
                esp_err_t result=send_message(&record.message,NULL,&record);
                /* kind 8 accounts its own pre-SDK rejection/SDK result exactly once. */
                if (result!=ESP_OK && !is_measurement(&record.message)) count_drop(false);
            }
        }
    }
}

static bool valid_uint(const cJSON *value, bool positive, uint32_t *out) {
    if (!cJSON_IsNumber(value) || !isfinite(value->valuedouble) || value->valuedouble < (positive ? 1.0 : 0.0) ||
        value->valuedouble > UINT32_MAX) return false;
    uint32_t converted = (uint32_t)value->valuedouble;
    if ((double)converted != value->valuedouble) return false;
    if (out) *out = converted;
    return true;
}

static cJSON *parse_json(const mesh_wire_message_t *message) {
    const char *end = NULL;
    cJSON *json = cJSON_ParseWithLengthOpts(message->payload, message->length + 1, &end, true);
    if (!cJSON_IsObject(json)) { cJSON_Delete(json); return NULL; }
    return json;
}

static bool valid_config(const mesh_wire_message_t *message) {
    cJSON *json = parse_json(message);
    if (!json) return false;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(json, "protocol_version");
    const cJSON *revision = cJSON_GetObjectItemCaseSensitive(json, "revision");
    const cJSON *logical = cJSON_GetObjectItemCaseSensitive(json, "logical_id");
    bool valid = cJSON_IsNumber(version) && version->valuedouble == 2 &&
                 valid_uint(revision, true, NULL) &&
                 (cJSON_IsNull(logical) || (cJSON_IsString(logical) && mesh_wire_valid_logical_id(logical->valuestring)));
    cJSON_Delete(json);
    return valid;
}

static void application_control(void *arg) {
    (void)arg;
    static control_t command;
    for (;;) {
        if (xQueueReceive(apply_configs, &command, portMAX_DELAY) != pdTRUE) continue;
        transport_state_t view = snapshot();
        if (command.epoch != view.session.epoch || !mesh_wire_is_fresh(command.queued_ms, now_ms()) ||
            strcmp(command.message.node_id, own_id) ||
            !(command.scan ? scan_config(&command.message) : valid_config(&command.message))) {
            count_drop(true);
            continue;
        }
        /* NVS/registration/sensor effects run only here. No Mesh RX or MQTT
         * callback waits for application configuration to complete. */
        if (command.scan) {
            scan_observe_topology();
            ab_runtime_command(command.message.payload,command.message.length,command.peer);
        } else node_messages_handle_config(command.message.payload, command.message.length);
    }
}

static bool mqtt_event_current(mqtt_context_t *context) {
    bool valid;
    portENTER_CRITICAL(&state_lock);
    valid = state.active_client == context->client && state.client_epoch == context->epoch &&
            state.session.epoch == context->epoch && root_ready(&state);
    portEXIT_CRITICAL(&state_lock);
    return valid;
}

static void mqtt_event(void *arg, esp_event_base_t base, int32_t id, void *event_data) {
    (void)base;
    mqtt_context_t *context = arg;
    esp_mqtt_event_handle_t event = event_data;
    if (!event || event->client != context->client || !mqtt_event_current(context)) return;
    switch (id) {
    case MQTT_EVENT_CONNECTED: {
        uint64_t fresh_nonce=((uint64_t)esp_random()<<32)|esp_random();
        if (!fresh_nonce) fresh_nonce=1;
        portENTER_CRITICAL(&state_lock);
        if (state.active_client == context->client && state.session.epoch == context->epoch) {
            if (!state.session.mqtt_connected) ++state.mqtt_generation;
            if (mesh_session_mqtt_event(&state.session, context->epoch, true))
                state.subscribe_pending = true;
            state.mqtt_disconnected_ms = 0;
            root_nonce=fresh_nonce;
        }
        portEXIT_CRITICAL(&state_lock);
        break;
    }
    case MQTT_EVENT_DISCONNECTED:
        context->assembler.valid = false;
        portENTER_CRITICAL(&state_lock);
        if (state.active_client == context->client && state.session.epoch == context->epoch) {
            if (state.session.mqtt_connected) {
                state.mqtt_disconnected_ms = now_ms();
                ++state.mqtt_generation;
            }
            mesh_session_mqtt_event(&state.session, context->epoch, false);
            root_nonce=0;
        }
        portEXIT_CRITICAL(&state_lock);
        break;
    case MQTT_EVENT_PUBLISHED:
        /* ESP-MQTT validates PUBACK against its QoS1 outbox before dispatch.
         * Our sole publisher uses QoS1 only for registration records, never
         * QoS2. QoS0 enqueue/CONNECTED cannot advance this broker progress. */
        portENTER_CRITICAL(&state_lock);
        if (event->msg_id > 0 && state.active_client == context->client &&
            state.session.epoch == context->epoch && root_ready(&state)) {
            state.last_puback_ms = now_ms();
            state.qos1_pending = false;
            saturating_increment(&state.status.mqtt_pubacks);
        }
        portEXIT_CRITICAL(&state_lock);
        break;
    case MQTT_EVENT_DATA: {
        if (event->current_data_offset < 0 || event->data_len < 0 ||
            event->total_data_len <= 0 || event->topic_len < 0) {
            context->assembler.valid = false;
            break;
        }
        if (event->current_data_offset == 0) context->fragment_queued_ms = now_ms();
        int result = mesh_wire_config_fragment(&context->assembler,
            event->topic, event->topic_len, event->data, event->data_len,
            event->total_data_len, event->current_data_offset);
        if (context->assembler.scan && event->retain) { context->assembler.valid=false; break; }
        if (result < 0) count_drop(true);
        else if (result == 1) {
            control_t command = {
                .message = context->assembler.message,
                .scan = context->assembler.scan,
                .epoch = context->epoch,
                .queued_ms = context->fragment_queued_ms,
            };
            if (!(command.scan ? root_scan_config(&command.message) : valid_config(&command.message)) ||
                xQueueSend(broker_configs, &command, 0) != pdTRUE) count_drop(true);
        }
        break;
    }
    default:
        break;
    }
    scan_observe_topology();
}

static void stop_client(mqtt_context_t *context) {
    portENTER_CRITICAL(&state_lock);
    root_nonce=0;
    state.active_client = NULL;
    state.session.mqtt_connected = false;
    state.subscribe_pending = false;
    /* Demand belongs to this Root tenure, not to a TCP/MQTT client. Even a
     * discarded outbox is not delivery progress; periodic registration will
     * supply a fresh record. Role change/stop or a valid PUBACK closes it. */
    state.mqtt_disconnected_ms = 0;
    portEXIT_CRITICAL(&state_lock);
    scan_observe_topology();
    if (context->client) {
        if (context->started) esp_mqtt_client_stop(context->client);
        /* Destroying discards the old Root's outbox; it is never handed to a
         * later role/lease. Called outside both SDK event handlers. */
        esp_mqtt_client_destroy(context->client);
    }
    memset(context, 0, sizeof(*context));
}

static bool start_client(mqtt_context_t *context, uint32_t epoch) {
    static char client_id[40];
    snprintf(client_id, sizeof(client_id), "omk-root-%s", own_id);
    const esp_mqtt_client_config_t config = {
        .broker.address.uri = MQTT_BROKER_URI,
        .credentials.client_id = client_id,
        .outbox.limit = MQTT_OUTBOX_BYTES,
        .network.timeout_ms = 5000,
        .network.reconnect_timeout_ms = 5000,
        .session.keepalive = 30,
        .buffer.size = 1536,
        /* No child Last Will: freshness is measured by Gateway receipt. */
    };
    context->epoch = epoch;
    context->client = esp_mqtt_client_init(&config);
    if (!context->client) return false;
    if (esp_mqtt_client_register_event(context->client, MQTT_EVENT_ANY, mqtt_event, context) != ESP_OK) {
        stop_client(context);
        return false;
    }
    portENTER_CRITICAL(&state_lock);
    bool valid = state.session.epoch == epoch && root_ready(&state);
    if (valid) {
        state.active_client = context->client;
        state.client_epoch = epoch;
        state.mqtt_disconnected_ms = now_ms();
        ++state.mqtt_generation;
    }
    portEXIT_CRITICAL(&state_lock);
    if (!valid || esp_mqtt_client_start(context->client) != ESP_OK) {
        stop_client(context);
        return false;
    }
    context->started = true;
    return true;
}

static bool valid_boot_id(const cJSON *item) {
    if (!cJSON_IsString(item) || strlen(item->valuestring) != 16) return false;
    for (size_t i = 0; i < 16; ++i) {
        char c = item->valuestring[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}

static bool read_status_identity(const cJSON *json, status_identity_t *identity) {
    const cJSON *boot = cJSON_GetObjectItemCaseSensitive(json, "boot_id");
    const cJSON *uptime = cJSON_GetObjectItemCaseSensitive(json, "uptime_s");
    if (!valid_boot_id(boot) || !valid_uint(cJSON_GetObjectItemCaseSensitive(json, "seq"), false, &identity->seq) ||
        !cJSON_IsNumber(uptime) || !isfinite(uptime->valuedouble) || uptime->valuedouble < 0 ||
        uptime->valuedouble > INT64_MAX / 1000000) return false;
    identity->uptime_s = (uint64_t)uptime->valuedouble;
    if ((double)identity->uptime_s != uptime->valuedouble) return false;
    memcpy(identity->boot_id, boot->valuestring, sizeof(identity->boot_id));
    return true;
}

static bool parse_status_identity(const mesh_wire_message_t *message, status_identity_t *identity) {
    cJSON *json = parse_json(message);
    if (!json) return false;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(json, "protocol_version");
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(json, "node_id");
    bool valid = cJSON_IsNumber(version) && version->valuedouble == 2 &&
        cJSON_IsString(id) && !strcmp(id->valuestring, message->node_id) &&
        valid_uint(cJSON_GetObjectItemCaseSensitive(json, "revision"), false, NULL) &&
        read_status_identity(json, identity);
    cJSON_Delete(json);
    return valid;
}

static bool receive_status_receipt(const mesh_wire_message_t *message, const uint8_t source[6]) {
    if (message->kind != MESH_MESSAGE_STATUS_RECEIPT || message->length > RECEIPT_PAYLOAD_MAX ||
        strcmp(message->node_id, own_id)) return false;
    status_identity_t identity;
    cJSON *json = parse_json(message);
    bool valid = json && read_status_identity(json, &identity);
    cJSON_Delete(json);
    if (!valid) return false;
    uint64_t now = now_ms(), proven_ms = identity.uptime_s * 1000;
    transport_state_t before = snapshot();
    bool root = esp_mesh_is_root();
    int layer = esp_mesh_get_layer();
    mesh_addr_t parent = {0};
    esp_err_t parent_result = esp_mesh_get_parent_bssid(&parent);
    portENTER_CRITICAL(&state_lock);
    delivery_state_t *d = &state.delivery;
    bool known = root_address_valid(state.root_mac);
    bool discover = !known;
    bool sent = false;
    for (unsigned i = 0; i < PROOF_BUCKETS + PROOF_EXTRA; ++i)
        if (sent_proofs[i].used && sent_proofs[i].seq == identity.seq &&
            sent_proofs[i].uptime_s == identity.uptime_s) sent = true;
    bool topology = parent_result == ESP_OK && layer > MESH_ROOT_LAYER &&
        layer == state.status.layer && !memcmp(parent.addr, state.status.parent_mac, 6) &&
        before.session.epoch == state.session.epoch &&
        before.parent_connection == state.parent_connection &&
        before.local_generation == state.local_generation;
    /* uptime_s truncates generation time: allow its subsecond quantization
     * error only in freshness, never add it to the delivery progress clock. */
    valid = state.session.started && !state.session.is_root && !root &&
        state.session.parent_connected && !state.session.rootless && child_delivery_demand(&state, now) &&
        root_address_valid(source) && memcmp(source, own_mac, 6) &&
        !state.voting && topology &&
        (discover ? sent : !memcmp(source, state.root_mac, 6)) &&
        !strcmp(identity.boot_id, d->boot_id) && d->submitted &&
        (uint32_t)(identity.seq - d->first_seq) <= (uint32_t)(d->last_sent_seq - d->first_seq) &&
        (!d->confirmed || seq_after(identity.seq, d->ack_seq)) &&
        identity.uptime_s >= d->first_uptime_s && proven_ms <= now &&
        now - proven_ms <= NATIVE_RECOVERY_RECEIPT_FRESH_MS + 999U && proven_ms >= d->proven_ms;
    if (valid) {
        if (discover) {
            /* Learning is allowed only while unknown in this connection episode.
             * A known address changes only through topology events, never merely
             * because another responder echoes a submitted identity. */
            remember_root_locked(source);
        }
        d->confirmed = true;
        d->ack_seq = identity.seq;
        d->proven_ms = proven_ms;
        ++d->progress;
        mq_receipt(&quality,proven_ms,now);
    }
    portEXIT_CRITICAL(&state_lock);
    if (valid) { scan_observe_topology(); mesh_diag_receipt(message->node_id, identity.boot_id, identity.seq); }
    return valid;
}

static bool publication_topic(const mesh_wire_message_t *message, char topic[MQTT_TOPIC_SIZE],
                              uint32_t *revision, status_identity_t *identity) {
    cJSON *json = parse_json(message);
    if (!json) return false;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(json, "protocol_version");
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(json, "node_id");
    bool valid = cJSON_IsNumber(version) && version->valuedouble == 2 &&
                 cJSON_IsString(id) && !strcmp(id->valuestring, message->node_id) &&
                 valid_uint(cJSON_GetObjectItemCaseSensitive(json, "revision"), false, revision) &&
                 valid_uint(cJSON_GetObjectItemCaseSensitive(json, "seq"), false, NULL) &&
                 valid_boot_id(cJSON_GetObjectItemCaseSensitive(json, "boot_id"));
    int written = -1;
    if (valid) {
        switch (message->kind) {
        case MESH_MESSAGE_SEN66: {
            const cJSON *device = cJSON_GetObjectItemCaseSensitive(json, "device_id");
            if (cJSON_IsString(device) && mesh_wire_valid_logical_id(device->valuestring))
                /* Gateway validates current Node ownership/revision before
                 * publishing to the shared logical-ID measurement topic. */
                written = snprintf(topic, MQTT_TOPIC_SIZE, "omk-relay/%s/sen66/raw", message->node_id);
            break;
        }
        case MESH_MESSAGE_BLE_RAW: {
            const cJSON *relay = cJSON_GetObjectItemCaseSensitive(json, "relay_node_id");
            if (cJSON_IsString(relay) && !strcmp(relay->valuestring, message->node_id))
                written = snprintf(topic, MQTT_TOPIC_SIZE, "omk-relay/%s/ble/raw", message->node_id);
            break;
        }
        case MESH_MESSAGE_NODE_STATUS:
            if (read_status_identity(json, identity))
                written = snprintf(topic, MQTT_TOPIC_SIZE, "omk/node/%s/status", message->node_id);
            break;
        case MESH_MESSAGE_REGISTRATION_STATUS:
            written = snprintf(topic, MQTT_TOPIC_SIZE, "omk/node/%s/registration/status", message->node_id);
            break;
        case MESH_MESSAGE_MEASUREMENT: {
            const cJSON *m=cJSON_GetObjectItemCaseSensitive(json,"m");
            if (message->length<=AB_JSON_MAX && cJSON_IsString(m) && strlen(m->valuestring)==344 &&
                m->valuestring[342]=='=' && m->valuestring[343]=='=')
                written=snprintf(topic,MQTT_TOPIC_SIZE,"omk/node/%s/measurement",message->node_id);
            break;
        }
        case MESH_MESSAGE_REGISTRATION_ACK:
            written = snprintf(topic, MQTT_TOPIC_SIZE, "omk/node/%s/registration/ack", message->node_id);
            break;
        default:
            break;
        }
    }
    cJSON_Delete(json);
    return written > 0 && written < MQTT_TOPIC_SIZE;
}

static origin_route_t *find_origin(origin_route_t routes[ROUTE_COUNT], const char *id, uint64_t time_ms) {
    for (size_t i = 0; i < ROUTE_COUNT; ++i)
        if (routes[i].used && !strcmp(routes[i].node_id, id) &&
            time_ms - routes[i].seen_ms < ROUTE_LIFETIME_MS) return &routes[i];
    return NULL;
}

static bool learn_origin(origin_route_t routes[ROUTE_COUNT], const publication_t *record,
                         uint32_t revision, uint64_t time_ms) {
    origin_route_t *route = find_origin(routes, record->message.node_id, time_ms);
    if (route && revision < route->revision) return false;
    if (!route) {
        for (size_t i = 0; i < ROUTE_COUNT; ++i) {
            if (!routes[i].used || time_ms - routes[i].seen_ms >= ROUTE_LIFETIME_MS) {
                route = &routes[i];
                break;
            }
        }
    }
    if (!route) return false;
    route->used = true;
    memcpy(route->node_id, record->message.node_id, sizeof(route->node_id));
    memcpy(route->mac, record->source, 6);
    route->revision = revision;
    route->seen_ms = time_ms;
    return true;
}

static void bridge_publication(mqtt_context_t *context, origin_route_t routes[ROUTE_COUNT],
                               const publication_t *record) {
    bool measurement=is_measurement(&record->message);
    transport_state_t view = snapshot();
    uint64_t time_ms = now_ms();
    if (!mesh_session_accept_record(&view.session, record->epoch, record->queued_ms, time_ms) ||
        !esp_mesh_is_root() ||
        (record->local && record->local_generation != view.local_generation)) {
        if (!measurement && !record->local) mesh_diag_root(&record->message, DIAG_ROOT_BRIDGE_REJECT);
        publication_drop(record,true);
        return;
    }
    char topic[MQTT_TOPIC_SIZE];
    uint32_t revision;
    status_identity_t identity;
    if (!publication_topic(&record->message, topic, &revision, &identity) ||
        (!measurement && !learn_origin(routes, record, revision, time_ms))) {
        if (!measurement && !record->local) mesh_diag_root(&record->message, DIAG_ROOT_BRIDGE_REJECT);
        publication_drop(record,true);
        return;
    }
    if (!record->local && record->message.kind == MESH_MESSAGE_NODE_STATUS) {
        /* This proves validated Root admission, even with no IP/MQTT. Reply to
         * the actual Mesh source, not the parent BSSID or a cached Node route. */
        status_receipt_t receipt = {.identity = identity, .queued_ms = time_ms, .epoch = record->epoch};
        memcpy(receipt.peer, record->source, sizeof(receipt.peer));
        memcpy(receipt.node_id, record->message.node_id, sizeof(receipt.node_id));
        if (xQueueSend(status_receipts, &receipt, 0) != pdTRUE) publication_drop(record,false);
    }
    /* Mapping is refreshed even while MQTT is reconnecting. No unbounded
     * backlog is held for a disconnected broker. Periodic status rebuilds it. */
    if (!context->client || !view.session.mqtt_connected || !mqtt_event_current(context)) {
        if (!measurement && !record->local) mesh_diag_root(&record->message, DIAG_ROOT_MQTT_OFFLINE);
        publication_drop(record,false);
        return;
    }
    bool registration = mesh_wire_is_registration(record->message.kind);
    if (registration) {
        portENTER_CRITICAL(&state_lock);
        if (state.active_client == context->client && state.session.epoch == context->epoch &&
            !state.qos1_pending) {
            state.qos1_pending = true;
            state.qos1_demand_ms = now_ms();
        }
        portEXIT_CRITICAL(&state_lock);
    }
    size_t limit = registration ? MQTT_OUTBOX_BYTES : MQTT_DATA_BYTES;
    size_t estimate = record->message.length + strlen(topic) + 16;
    if ((size_t)esp_mqtt_client_get_outbox_size(context->client) + estimate > limit) {
        if (!measurement && !record->local) mesh_diag_root(&record->message, DIAG_ROOT_MQTT_FULL);
        publication_drop(record,false);
        return;
    }
    /* This task is the sole publisher, so the preflight bound cannot race
     * another enqueue. ESP-MQTT sends asynchronously and expires old records. */
    int result = esp_mqtt_client_enqueue(context->client, topic, record->message.payload,
                                         record->message.length, registration ? 1 : 0,
                                         registration ? 1 : 0, true);
    if (!measurement && !record->local) mesh_diag_root(&record->message,
        result < 0 ? DIAG_ROOT_MQTT_ERROR : DIAG_ROOT_MQTT_ACCEPT);
    if (result < 0) publication_drop(record,false);
    else {
        portENTER_CRITICAL(&state_lock);
        if (!measurement) ++state.status.mqtt_published;
        portEXIT_CRITICAL(&state_lock);
    }
}

static void route_config(origin_route_t routes[ROUTE_COUNT], control_t *command) {
    transport_state_t view = snapshot();
    if (!root_ready(&view) || command->epoch != view.session.epoch || !view.session.mqtt_connected ||
        !esp_mesh_is_root() || !mesh_wire_is_fresh(command->queued_ms, now_ms()) ||
        !(command->scan ? root_scan_config(&command->message) : valid_config(&command->message))) {
        count_drop(true);
        return;
    }
    if (!strcmp(command->message.node_id, own_id)) {
        memcpy(command->peer, own_mac, 6);
        if (xQueueSend(apply_configs, command, 0) != pdTRUE) count_drop(true);
        return;
    }
    origin_route_t *route = find_origin(routes, command->message.node_id, now_ms());
    if (!route) {
        /* Gateway owns the desired revision and repeats it on fresh state.
         * We deliberately do not build a pending-config or retry cache. */
        count_drop(true);
        return;
    }
    memcpy(command->peer, route->mac, 6);
    if (xQueueSend(mesh_configs, command, 0) != pdTRUE) count_drop(true);
}

static void reconcile_expired_vote(void) {
    transport_state_t before = snapshot();
    uint64_t now = now_ms();
    if (!before.session.started || !before.voting || now < before.vote_hold_until ||
        now < before.inhibit_until) return;
    /* VOTE_STOPPED can be lost. Re-read SDK topology outside locks and grant
     * the existing 120 s settling grace before allowing recovery again. */
    bool root = esp_mesh_is_root();
    mesh_addr_t parent = {0};
    int layer = before.parent_connection ? esp_mesh_get_layer() : -1;
    esp_err_t parent_result = before.parent_connection ? esp_mesh_get_parent_bssid(&parent) : ESP_FAIL;
    /* A failed parent query alone is not proof of a lost link. Invalidate only
     * observed contradictions, so a healthy Root need not drop its MQTT lease. */
    bool topology_changed = root != before.session.is_root ||
        (before.parent_connection && layer <= 0 && before.session.parent_connected) ||
        (parent_result == ESP_OK && layer > 0 && (!before.session.parent_connected ||
         (layer == MESH_ROOT_LAYER) != root || memcmp(parent.addr, before.status.parent_mac, 6)));
    portENTER_CRITICAL(&state_lock);
    bool current = state.session.started && state.session.epoch == before.session.epoch &&
                   state.voting && state.vote_hold_until == before.vote_hold_until &&
                   now >= state.inhibit_until;
    if (current) {
        state.voting = false;
        if (!state.session.is_root) {
            /* Match VOTE_STOPPED even if ROOT_ADDRESS arrived during voting:
             * an upstream Root change need not change our direct parent. */
            state.delivery.offered = false;
            memset(state.root_mac, 0, sizeof(state.root_mac));
            state.session.gateway_reachable = false;
        }
        state.inhibit_until = now + 120000;
        if (topology_changed) {
            /* Do not synthesize connection events or run DHCP transitions
             * concurrently with the real event task. Discard old evidence;
             * normal events and the existing SDK-role guard remain authoritative. */
            invalidate_link_locked();
            memset(state.root_mac, 0, sizeof(state.root_mac));
        }
    }
    portEXIT_CRITICAL(&state_lock);
}

static void recovery_tick(native_recovery_t *controller, mqtt_context_t *context) {
    reconcile_expired_vote();
    scan_observe_topology();
    transport_state_t view = snapshot();
    portENTER_CRITICAL(&state_lock);
    mesh_quality_view_t q=mq_evaluate(&quality,now_ms(),state.session.started && !state.session.is_root &&
        state.session.parent_connected && !state.session.rootless && !state.voting);
    portEXIT_CRITICAL(&state_lock);
    mesh_probe_quality(&q);
    mesh_diag_quality(q.bad);
    native_recovery_input_t input = {
        .epoch = view.session.epoch, .mqtt_generation = view.mqtt_generation,
        .started = view.session.started,
        .root = view.session.is_root, .parent = view.session.parent_connected,
        .rootless = view.session.rootless, .ip = view.session.has_ip,
        .mqtt = view.session.mqtt_connected, .client = context->client != NULL,
        .qos1_pending = view.qos1_pending, .voting = view.voting,
        .inhibit_until = view.inhibit_until, .last_ack_ms = view.last_puback_ms,
        .demand_since_ms = view.qos1_demand_ms,
        .disconnected_since_ms = view.mqtt_disconnected_ms,
        .weak_parent_losses = weak_parent_losses(&view, now_ms()),
        .tx_demand = child_tx_demand(&view, now_ms()), .tx_progress = view.tx_progress,
        .delivery_demand = child_delivery_demand(&view, now_ms()),
        .delivery_generation = view.delivery_generation, .delivery_proven_ms = view.delivery.proven_ms,
        .quality_bad=q.bad,
    };
    mesh_diag_demand(input.tx_demand, input.delivery_demand, input.delivery_proven_ms,
                     input.inhibit_until, input.voting);
    bool observed_link = false;
    uint8_t previous_parent[6] = {0}, next_parent[6] = {0};
    portENTER_CRITICAL(&state_lock);
    if (state.recovery_wait_link && (now_ms() > state.recovery_link_until ||
        state.session.is_root != state.recovery_link_root))
        state.recovery_wait_link = false;
    if (state.recovery_wait_link && state.parent_connection != state.recovery_link_generation && state.session.started &&
        state.session.parent_connected && !state.session.rootless) {
        saturating_increment(&state.status.recovery_link);
        state.recovery_wait_link = false;
        observed_link = !state.recovery_link_root;
        memcpy(previous_parent, state.recovery_parent_bssid, 6);
        memcpy(next_parent, state.status.parent_mac, 6);
    }
    portEXIT_CRITICAL(&state_lock);
    if (observed_link)
        ESP_LOGI(TAG, "Parent after recovery: " MACSTR " -> " MACSTR " same=%d",
                 MAC2STR(previous_parent), MAC2STR(next_parent),
                 !memcmp(previous_parent, next_parent, 6));
    if(repair_tick(&q)) return;
    native_recovery_action_t action = native_recovery_select(controller, now_ms(), &input);
    if (action == REC_NONE) return;
    recovery_request_t request = {.action = action, .epoch = view.session.epoch,
                                  .progress_ms = view.last_puback_ms,
                                  .mqtt_generation = view.mqtt_generation,
                                  .parent_connection = view.parent_connection,
                                  .repeated_loss = action == REC_CHILD_RESELECT &&
                                                   input.parent && !input.rootless && input.weak_parent_losses,
                                  .tx_stall = (controller->child_causes & REC_CHILD_TX_STALL) != 0,
                                  .delivery_stall = (controller->child_causes & REC_CHILD_DELIVERY_STALL) != 0,
                                  .quality_bad = (controller->child_causes & REC_CHILD_QUALITY) != 0,
                                  .delivery_progress = view.delivery.progress,
                                  .tx_progress = view.tx_progress, .local_generation = view.local_generation};
    memcpy(request.parent_bssid, view.status.parent_mac, 6);
    portENTER_CRITICAL(&state_lock);
    state.status.recovery_action = action;
    saturating_increment(&state.status.recovery_requests);
    portEXIT_CRITICAL(&state_lock);
    mesh_diag_recovery(action,
        (request.tx_stall ? DIAG_REC_TX : 0) | (request.delivery_stall ? DIAG_REC_DELIVERY : 0) |
        (request.repeated_loss ? DIAG_REC_REPEATED : 0), false, 0);
    if(action==REC_CHILD_REPAIR) {
        repair_start(&request);
        (void)repair_tick(&q);
    } else if (action == REC_MQTT_RECONNECT || action == REC_MQTT_RECREATE) {
        /* Only this existing owner stops/destroys clients. Automatic retries
         * and keepalive run first. A fresh PUBACK cancels a selected request. */
        esp_err_t result = ESP_ERR_INVALID_STATE;
        if (recovery_valid(&request) && context->client && mqtt_event_current(context)) {
            if (action == REC_MQTT_RECONNECT) {
                if (recovery_valid(&request))
                    result = esp_mqtt_client_disconnect(context->client);
            } else {
                portENTER_CRITICAL(&state_lock);
                bool current = state.session.epoch == request.epoch &&
                               state.last_puback_ms == request.progress_ms &&
                               state.mqtt_generation == request.mqtt_generation &&
                               (!state.session.mqtt_connected || state.qos1_pending) && root_ready(&state);
                if (current) {
                    ++state.session.epoch;
                    state.session.mqtt_connected = false;
                    state.session.gateway_reachable = false;
                    state.active_client = NULL;
                }
                portEXIT_CRITICAL(&state_lock);
                if (current) {
                    stop_client(context); /* bounded old outbox is discarded */
                    result = ESP_OK; /* normal owner loop creates the next generation */
                }
            }
        }
        recovery_finished(&request, result);
    } else {
        recover_mesh(&request);
    }
}

static void mqtt_bridge(void *arg) {
    (void)arg;
    static mqtt_context_t context;
    static origin_route_t routes[ROUTE_COUNT];
    static publication_t record;
    static control_t command;
    static unsigned publication_turn;
    native_recovery_t recovery;
    /* Stable device-specific staggering; no random state, NVS or new task. */
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < sizeof(own_mac); ++i) hash = (hash ^ own_mac[i]) * 16777619U;
    native_recovery_init(&recovery, hash % 30001U);
    uint32_t map_epoch = UINT32_MAX;
    uint64_t retry_client_after = 0;
    uint64_t check_lease_after = 0;
    bool posted_tods = false;
    bool tods_valid = false;
    for (;;) {
        if (now_ms() >= check_lease_after) {
            /* IDF6 Mesh netifs may omit GOT_IP after reacquiring the same tuple.
             * This is state reconciliation in the existing owner, not DHCP or
             * Mesh recovery: no restart, new task or election is requested. */
            reconcile_root_lease(NULL);
            recovery_tick(&recovery, &context);
            check_lease_after = now_ms() + LEASE_CHECK_INTERVAL_MS;
        }
        transport_state_t view = snapshot();
        if (view.session.epoch != map_epoch) {
            memset(routes, 0, sizeof(routes));
            map_epoch = view.session.epoch;
            tods_valid = false;
        }
        bool ready = root_ready(&view) && esp_mesh_is_root();
        if (context.client && (!ready || context.epoch != view.session.epoch)) stop_client(&context);
        if (ready && !context.client && now_ms() >= retry_client_after) {
            if (!start_client(&context, view.session.epoch)) retry_client_after = now_ms() + 5000;
        }
        view = snapshot();
        if (context.client && view.session.mqtt_connected && view.subscribe_pending && mqtt_event_current(&context)) {
            int result = esp_mqtt_client_subscribe(context.client, "omk/node/+/registration/config", 1);
            int scan_result=esp_mqtt_client_subscribe(context.client,"omk/node/+/ble_scan/config",1);
            if (result >= 0 && scan_result >= 0) {
                portENTER_CRITICAL(&state_lock);
                if (state.session.epoch == context.epoch) state.subscribe_pending = false;
                portEXIT_CRITICAL(&state_lock);
            }
        }
        bool reachable = root_ready(&view) && view.session.mqtt_connected;
        if (view.session.started && view.session.is_root && esp_mesh_is_root() &&
            (!tods_valid || posted_tods != reachable)) {
            if (esp_mesh_post_toDS_state(reachable) == ESP_OK) {
                posted_tods = reachable;
                tods_valid = true;
            }
        }
        if (next_publication(root_registration, root_status, root_data, &publication_turn, &record))
            bridge_publication(&context, routes, &record);
        if (xQueueReceive(broker_configs, &command, 0) == pdTRUE) route_config(routes, &command);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

esp_err_t mesh_transport_init(void) {
    if (initialized) return ESP_ERR_INVALID_STATE;
    esp_err_t error = node_identity_get_id_hex(own_id, sizeof(own_id));
    if (error != ESP_OK) return error;
    error = esp_read_mac(own_mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    error = esp_read_mac(state.status.self_ap_mac, ESP_MAC_WIFI_SOFTAP);
    if (error != ESP_OK) return error;
    local_data = xQueueCreate(LOCAL_DATA_COUNT, sizeof(publication_t));
    local_status = xQueueCreate(LOCAL_STATUS_COUNT, sizeof(publication_t));
    local_registration = xQueueCreate(CONTROL_COUNT, sizeof(publication_t));
    root_data = xQueueCreate(ROOT_DATA_COUNT, sizeof(publication_t));
    root_status = xQueueCreate(ROOT_STATUS_COUNT, sizeof(publication_t));
    root_registration = xQueueCreate(CONTROL_COUNT, sizeof(publication_t));
    broker_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    mesh_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    apply_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    status_receipts = xQueueCreate(RECEIPT_COUNT, sizeof(status_receipt_t));
    if (!local_data || !local_status || !local_registration || !root_data || !root_status || !root_registration ||
        !broker_configs || !mesh_configs || !apply_configs || !status_receipts) return ESP_ERR_NO_MEM;
    /* Standard native-Mesh netifs: no internal DHCP server, NAPT, DNS, or
     * role-dependent netif destruction/recreation. */
    error = esp_netif_create_default_wifi_mesh_netifs(&netif_sta, NULL);
    if (error != ESP_OK) return error;
    error = esp_event_handler_register(MESH_EVENT, ESP_EVENT_ANY_ID, mesh_event, NULL);
    if (error != ESP_OK) return error;
    error = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, ip_event, NULL);
    if (error != ESP_OK) return error;
    error = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_LOST_IP, ip_event, NULL);
    if (error != ESP_OK) return error;
    state.status.layer = -1;
    state.session.rootless = true;
    if (xTaskCreate(mesh_receiver, "mesh_rx", 4096, NULL, 5, &rx_task_handle) != pdPASS ||
        xTaskCreate(mesh_sender, "mesh_tx", 6144, NULL, 4, &tx_task_handle) != pdPASS ||
        xTaskCreate(mqtt_bridge, "root_mqtt", 6144, NULL, 4, &mqtt_task_handle) != pdPASS ||
        xTaskCreate(application_control, "node_config", 4096, NULL, 3, &control_task_handle) != pdPASS)
        return ESP_ERR_NO_MEM;
    initialized = true;
    return ESP_OK;
}

esp_err_t mesh_transport_start(void) {
    if (!initialized || snapshot().session.started) return ESP_ERR_INVALID_STATE;
    omk_gateway_credentials_t gateway;
    esp_err_t error = gateway_credentials_load(&gateway);
    if (error != ESP_OK) return error;
    uint8_t mesh_id[6];
    char password[OMK_MESH_AP_PASSWORD_LENGTH + 1];
    error = mesh_credentials_derive(gateway.ssid, gateway.ssid_length, gateway.psk, gateway.psk_length,
                                    mesh_id, password);
    if (error != ESP_OK) return error;
    error = esp_wifi_set_ps(WIFI_PS_NONE);
    if (error != ESP_OK) return error;
    error = esp_wifi_start();
    if (error != ESP_OK) return error;
    error = esp_mesh_init();
    if (error != ESP_OK) return error;
    /* IDF 6.0.1 defaults to allowing multiple Roots. Restore OMK's former
     * SDK policy, without fixing a Root or implementing election logic.
     * Fail before mesh_start if the policy cannot be applied/verified.
     * main keeps sensing/BLE alive; no restart or automatic retry is added. */
    error = esp_mesh_allow_root_conflicts(false);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "Could not disable Mesh root conflicts: %s", esp_err_to_name(error));
        return error;
    }
    if (esp_mesh_is_root_conflicts_allowed()) {
        ESP_LOGE(TAG, "Mesh root conflicts remain enabled after configuration");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Mesh root conflicts: disabled");
    error = esp_mesh_set_max_layer(CONFIG_MESH_MAX_LAYER);
    if (error != ESP_OK) return error;
    error = esp_mesh_set_ap_assoc_expire(30);
    if (error != ESP_OK) return error;
    error = esp_mesh_send_block_time(5000);
    if (error != ESP_OK) return error;
    error = esp_mesh_set_ap_authmode(WIFI_AUTH_WPA2_PSK);
    if (error != ESP_OK) return error;
    mesh_cfg_t config = MESH_INIT_CONFIG_DEFAULT();
    memcpy(config.mesh_id.addr, mesh_id, sizeof(mesh_id));
    config.channel = 0;
    config.router.ssid_len = gateway.ssid_length;
    memcpy(config.router.ssid, gateway.ssid, gateway.ssid_length);
    memcpy(config.router.password, gateway.psk, gateway.psk_length);
    config.mesh_ap.max_connection = 6;
    config.mesh_ap.nonmesh_max_connection = 0;
    memcpy(config.mesh_ap.password, password, OMK_MESH_AP_PASSWORD_LENGTH);
    error = esp_mesh_set_config(&config);
    memset(&gateway, 0, sizeof(gateway));
    memset(password, 0, sizeof(password));
    memset(config.router.password, 0, sizeof(config.router.password));
    memset(config.mesh_ap.password, 0, sizeof(config.mesh_ap.password));
    if (error != ESP_OK) return error;
    /* Self-organized parent/root selection and standard SDK recovery only. */
    return esp_mesh_start();
}
