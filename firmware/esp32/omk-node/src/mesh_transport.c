#include "mesh_transport.h"

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
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "gateway_credentials.h"
#include "mesh_credentials.h"
#include "mesh_session.h"
#include "mqtt_client.h"
#include "node_identity.h"
#include "node_messages.h"

/* These queues, the SDK Mesh queues and the bounded MQTT outbox are the only
 * transport storage. There is no application retry/ACK or routing protocol. */
#define LOCAL_DATA_COUNT 4
#define ROOT_DATA_COUNT 8
#define CONTROL_COUNT 2
#define ROUTE_COUNT CONFIG_MESH_ROUTE_TABLE_SIZE
#define ROUTE_LIFETIME_MS 120000U
#define MQTT_OUTBOX_BYTES 8192U
#define MQTT_DATA_BYTES 4096U
#define MQTT_TOPIC_SIZE 96
#define MQTT_BROKER_URI "mqtt://192.168.50.1:1883"

static const char *TAG = "omk-transport";

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
    uint64_t queued_ms;
    uint32_t epoch;
} control_t;

typedef struct {
    mesh_transport_status_t status; /* Counters, layer and parent MAC. */
    mesh_session_t session;
    uint8_t root_mac[6];
    uint32_t local_generation;
    esp_mqtt_client_handle_t active_client;
    uint32_t client_epoch;
    bool subscribe_pending;
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
static bool initialized;
static esp_netif_t *netif_sta;
static char own_id[13];
static uint8_t own_mac[6];
static QueueHandle_t local_data, local_registration;
static QueueHandle_t root_data, root_registration;
static QueueHandle_t broker_configs, mesh_configs, apply_configs;
static TaskHandle_t rx_task_handle, tx_task_handle, mqtt_task_handle, control_task_handle;

static uint64_t now_ms(void) { return (uint64_t)esp_timer_get_time() / 1000; }

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
    portEXIT_CRITICAL(&state_lock);
}

void mesh_transport_drop_pending(void) {
    if (!initialized) return;
    portENTER_CRITICAL(&state_lock);
    ++state.local_generation;
    portEXIT_CRITICAL(&state_lock);
    /* A worker may already own a record; its generation check invalidates it.
     * A packet already passed to the SDK can still arrive. Gateway revisions
     * therefore remain authoritative, including after Root changes. */
    xQueueReset(local_data);
    xQueueReset(local_registration);
}

esp_err_t mesh_transport_publish(mesh_message_kind_t kind, const char *payload, size_t length) {
    if (!initialized) return ESP_ERR_INVALID_STATE;
    if (kind < MESH_MESSAGE_SEN66 || kind > MESH_MESSAGE_REGISTRATION_ACK || !payload)
        return ESP_ERR_INVALID_ARG;
    if (!length || length > MESH_TRANSPORT_PAYLOAD_MAX || memchr(payload, '\0', length))
        return ESP_ERR_INVALID_SIZE;
    publication_t record = {0};
    record.message.kind = kind;
    record.message.length = (uint16_t)length;
    memcpy(record.message.node_id, own_id, sizeof(own_id));
    memcpy(record.message.payload, payload, length);
    record.message.payload[length] = '\0';
    record.local = true;
    record.queued_ms = now_ms();
    portENTER_CRITICAL(&state_lock);
    record.local_generation = state.local_generation;
    portEXIT_CRITICAL(&state_lock);
    QueueHandle_t queue = mesh_wire_is_registration(kind) ? local_registration : local_data;
    if (xQueueSend(queue, &record, 0) != pdTRUE) {
        count_drop(false);
        return ESP_ERR_NO_MEM;
    }
    portENTER_CRITICAL(&state_lock);
    ++state.status.tx_accepted;
    portEXIT_CRITICAL(&state_lock);
    return ESP_OK;
}

static bool root_ready(const transport_state_t *s) {
    return mesh_session_root_ready(&s->session);
}

/* Called only on the default event loop. The netif is never replaced. A role
 * or link change invalidates old MQTT callbacks and queued remote records. */
static void invalidate_link_locked(void) {
    mesh_session_invalidate(&state.session);
}

static void observe_role(bool root) {
    bool changed;
    portENTER_CRITICAL(&state_lock);
    changed = mesh_session_set_role(&state.session, root);
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
    if (id != MESH_EVENT_STOPPED) observe_role(esp_mesh_is_root());
    switch (id) {
    case MESH_EVENT_STARTED:
        portENTER_CRITICAL(&state_lock);
        mesh_session_started(&state.session);
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_STOPPED:
        portENTER_CRITICAL(&state_lock);
        mesh_session_stopped(&state.session);
        portEXIT_CRITICAL(&state_lock);
        esp_netif_dhcpc_stop(netif_sta);
        break;
    case MESH_EVENT_PARENT_CONNECTED: {
        const mesh_event_connected_t *event = data;
        bool root = esp_mesh_is_root();
        if ((event->self_layer == MESH_ROOT_LAYER) != root) break;
        portENTER_CRITICAL(&state_lock);
        mesh_session_parent_connected(&state.session, root);
        state.status.layer = event->self_layer;
        memcpy(state.status.parent_mac, event->connected.bssid, 6);
        portEXIT_CRITICAL(&state_lock);
        esp_netif_dhcpc_stop(netif_sta);
        if (root) {
            esp_err_t error = esp_netif_dhcpc_start(netif_sta);
            if (error != ESP_OK) ESP_LOGW(TAG, "Root DHCP start: %s", esp_err_to_name(error));
        }
        ESP_LOGI(TAG, "Parent connected, layer=%d root=%d", event->self_layer, root);
        break;
    }
    case MESH_EVENT_PARENT_DISCONNECTED:
        portENTER_CRITICAL(&state_lock);
        mesh_session_parent_disconnected(&state.session);
        state.status.layer = -1;
        memset(state.status.parent_mac, 0, 6);
        portEXIT_CRITICAL(&state_lock);
        esp_netif_dhcpc_stop(netif_sta);
        break;
    case MESH_EVENT_LAYER_CHANGE:
        portENTER_CRITICAL(&state_lock);
        state.status.layer = ((mesh_event_layer_change_t *)data)->new_layer;
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_ROOT_ADDRESS: {
        const mesh_event_root_address_t *root = data;
        portENTER_CRITICAL(&state_lock);
        if (memcmp(state.root_mac, root->addr, 6)) {
            memcpy(state.root_mac, root->addr, 6);
            if (!state.session.is_root) {
                ++state.session.epoch;
                state.session.gateway_reachable = false;
            }
        }
        portEXIT_CRITICAL(&state_lock);
        break;
    }
    case MESH_EVENT_NETWORK_STATE:
        portENTER_CRITICAL(&state_lock);
        mesh_session_set_rootless(&state.session, ((mesh_event_network_state_t *)data)->is_rootless);
        portEXIT_CRITICAL(&state_lock);
        break;
    case MESH_EVENT_TODS_STATE:
        portENTER_CRITICAL(&state_lock);
        state.session.gateway_reachable = *(mesh_event_toDS_state_t *)data == MESH_TODS_REACHABLE;
        portEXIT_CRITICAL(&state_lock);
        break;
    default:
        break;
    }
}

static void ip_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    (void)base;
    transport_state_t before = snapshot();
    if (!before.session.is_root || !before.session.parent_connected || !esp_mesh_is_root()) return;
    esp_netif_ip_info_t current = {0};
    if (esp_netif_get_ip_info(netif_sta, &current) != ESP_OK) return;
    if (id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        esp_netif_dhcp_status_t dhcp_status;
        if (!event || event->esp_netif != netif_sta || !current.ip.addr ||
            current.ip.addr != event->ip_info.ip.addr || !esp_netif_is_netif_up(netif_sta) ||
            esp_netif_dhcpc_get_status(netif_sta, &dhcp_status) != ESP_OK ||
            dhcp_status != ESP_NETIF_DHCP_STARTED) return;
        portENTER_CRITICAL(&state_lock);
        mesh_session_got_ip(&state.session, before.session.epoch);
        portEXIT_CRITICAL(&state_lock);
    } else if (id == IP_EVENT_STA_LOST_IP && !current.ip.addr) {
        /* A delayed LOST_IP must not invalidate a newer valid lease. */
        portENTER_CRITICAL(&state_lock);
        if (state.session.epoch == before.session.epoch) invalidate_link_locked();
        portEXIT_CRITICAL(&state_lock);
    }
}

static bool enqueue_root(publication_t *record, const transport_state_t *view) {
    record->epoch = view->session.epoch;
    QueueHandle_t queue = mesh_wire_is_registration(record->message.kind) ? root_registration : root_data;
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
        if (error != ESP_OK) {
            if (error != ESP_ERR_MESH_TIMEOUT) vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (data.proto != MESH_PROTO_BIN || !mesh_wire_decode(buffer, data.size, &record.message)) {
            count_drop(true);
            continue;
        }
        transport_state_t view = snapshot();
        record.queued_ms = now_ms();
        record.local = false;
        record.local_generation = 0;
        memcpy(record.source, from.addr, 6);
        if (record.message.kind == MESH_MESSAGE_CONFIG) {
            if (view.session.is_root || !view.session.parent_connected || view.session.rootless ||
                strcmp(record.message.node_id, own_id) || memcmp(from.addr, view.root_mac, 6)) {
                count_drop(true);
                continue;
            }
            command.message = record.message;
            command.queued_ms = record.queued_ms;
            command.epoch = view.session.epoch;
            memcpy(command.peer, from.addr, 6);
            if (xQueueSend(apply_configs, &command, 0) != pdTRUE) count_drop(true);
        } else if (!mesh_session_accept_ingress(&view.session) || !esp_mesh_is_root() || !enqueue_root(&record, &view)) {
            count_drop(true);
        }
    }
}

static esp_err_t send_message(const mesh_wire_message_t *message, const uint8_t *destination) {
    /* Only mesh_sender calls this function: esp_mesh_send is non-reentrant. */
    static uint8_t buffer[MESH_WIRE_MAX_SIZE];
    size_t length;
    if (!mesh_wire_encode(message, buffer, sizeof(buffer), &length)) return ESP_ERR_INVALID_ARG;
    mesh_data_t data = {
        .data = buffer, .size = (uint16_t)length,
        .proto = MESH_PROTO_BIN, .tos = MESH_TOS_P2P,
    };
    mesh_addr_t to;
    if (destination) memcpy(to.addr, destination, sizeof(to.addr));
    /* NULL addresses the current Root application. Like the IDF example this
     * uses its ordinary recv queue, not external IPv4:PORT / recv_toDS. */
    return esp_mesh_send(destination ? &to : NULL, &data,
                         destination ? MESH_DATA_P2P : MESH_DATA_TODS, NULL, 0);
}

static void mesh_sender(void *arg) {
    (void)arg;
    static publication_t record;
    static control_t command;
    for (;;) {
        if (xQueueReceive(mesh_configs, &command, 0) == pdTRUE) {
            transport_state_t view = snapshot();
            if (!root_ready(&view) || command.epoch != view.session.epoch || !esp_mesh_is_root() ||
                !mesh_wire_is_fresh(command.queued_ms, now_ms()) ||
                send_message(&command.message, command.peer) != ESP_OK) count_drop(false);
        }
        if (xQueueReceive(local_registration, &record, 0) != pdTRUE &&
            xQueueReceive(local_data, &record, pdMS_TO_TICKS(20)) != pdTRUE) continue;
        transport_state_t view = snapshot();
        if (!view.session.started || record.local_generation != view.local_generation ||
            !mesh_wire_is_fresh(record.queued_ms, now_ms())) {
            count_drop(false);
            continue;
        }
        if (view.session.is_root && esp_mesh_is_root()) {
            memcpy(record.source, own_mac, 6);
            if (!enqueue_root(&record, &view)) count_drop(false);
        } else {
            /* toDS is advisory. A same-Root parent reconnect need not produce
             * a new toDS transition; it must not leave telemetry gated forever.
             * Root drains and drops immediately when MQTT is unavailable. */
            if (!view.session.parent_connected || view.session.rootless ||
                send_message(&record.message, NULL) != ESP_OK) count_drop(false);
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
            strcmp(command.message.node_id, own_id) || !valid_config(&command.message)) {
            count_drop(true);
            continue;
        }
        /* NVS/registration/sensor effects run only here. No Mesh RX or MQTT
         * callback waits for application configuration to complete. */
        node_messages_handle_config(command.message.payload, command.message.length);
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
    case MQTT_EVENT_CONNECTED:
        portENTER_CRITICAL(&state_lock);
        if (state.active_client == context->client &&
            mesh_session_mqtt_event(&state.session, context->epoch, true))
            state.subscribe_pending = true;
        portEXIT_CRITICAL(&state_lock);
        break;
    case MQTT_EVENT_DISCONNECTED:
        context->assembler.valid = false;
        portENTER_CRITICAL(&state_lock);
        if (state.active_client == context->client)
            mesh_session_mqtt_event(&state.session, context->epoch, false);
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
        if (result < 0) count_drop(true);
        else if (result == 1) {
            control_t command = {
                .message = context->assembler.message,
                .epoch = context->epoch,
                .queued_ms = context->fragment_queued_ms,
            };
            if (xQueueSend(broker_configs, &command, 0) != pdTRUE) count_drop(true);
        }
        break;
    }
    default:
        break;
    }
}

static void stop_client(mqtt_context_t *context) {
    portENTER_CRITICAL(&state_lock);
    state.active_client = NULL;
    state.session.mqtt_connected = false;
    state.subscribe_pending = false;
    portEXIT_CRITICAL(&state_lock);
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

static bool publication_topic(const mesh_wire_message_t *message, char topic[MQTT_TOPIC_SIZE], uint32_t *revision) {
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
            written = snprintf(topic, MQTT_TOPIC_SIZE, "omk/node/%s/status", message->node_id);
            break;
        case MESH_MESSAGE_REGISTRATION_STATUS:
            written = snprintf(topic, MQTT_TOPIC_SIZE, "omk/node/%s/registration/status", message->node_id);
            break;
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
    transport_state_t view = snapshot();
    uint64_t time_ms = now_ms();
    if (!mesh_session_accept_record(&view.session, record->epoch, record->queued_ms, time_ms) ||
        !esp_mesh_is_root() ||
        (record->local && record->local_generation != view.local_generation)) {
        count_drop(true);
        return;
    }
    char topic[MQTT_TOPIC_SIZE];
    uint32_t revision;
    if (!publication_topic(&record->message, topic, &revision) ||
        !learn_origin(routes, record, revision, time_ms)) {
        count_drop(true);
        return;
    }
    /* Mapping is refreshed even while MQTT is reconnecting. No unbounded
     * backlog is held for a disconnected broker. Periodic status rebuilds it. */
    if (!context->client || !view.session.mqtt_connected || !mqtt_event_current(context)) {
        count_drop(false);
        return;
    }
    bool registration = mesh_wire_is_registration(record->message.kind);
    size_t limit = registration ? MQTT_OUTBOX_BYTES : MQTT_DATA_BYTES;
    size_t estimate = record->message.length + strlen(topic) + 16;
    if ((size_t)esp_mqtt_client_get_outbox_size(context->client) + estimate > limit) {
        count_drop(false);
        return;
    }
    /* This task is the sole publisher, so the preflight bound cannot race
     * another enqueue. ESP-MQTT sends asynchronously and expires old records. */
    int result = esp_mqtt_client_enqueue(context->client, topic, record->message.payload,
                                         record->message.length, registration ? 1 : 0,
                                         registration ? 1 : 0, true);
    if (result < 0) count_drop(false);
    else {
        portENTER_CRITICAL(&state_lock);
        ++state.status.mqtt_published;
        portEXIT_CRITICAL(&state_lock);
    }
}

static void route_config(origin_route_t routes[ROUTE_COUNT], control_t *command) {
    transport_state_t view = snapshot();
    if (!root_ready(&view) || command->epoch != view.session.epoch || !view.session.mqtt_connected ||
        !esp_mesh_is_root() || !mesh_wire_is_fresh(command->queued_ms, now_ms()) ||
        !valid_config(&command->message)) {
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

static void mqtt_bridge(void *arg) {
    (void)arg;
    static mqtt_context_t context;
    static origin_route_t routes[ROUTE_COUNT];
    static publication_t record;
    static control_t command;
    uint32_t map_epoch = UINT32_MAX;
    uint64_t retry_client_after = 0;
    bool posted_tods = false;
    bool tods_valid = false;
    for (;;) {
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
            if (result >= 0) {
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
        if (xQueueReceive(root_registration, &record, 0) == pdTRUE ||
            xQueueReceive(root_data, &record, 0) == pdTRUE) bridge_publication(&context, routes, &record);
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
    local_data = xQueueCreate(LOCAL_DATA_COUNT, sizeof(publication_t));
    local_registration = xQueueCreate(CONTROL_COUNT, sizeof(publication_t));
    root_data = xQueueCreate(ROOT_DATA_COUNT, sizeof(publication_t));
    root_registration = xQueueCreate(CONTROL_COUNT, sizeof(publication_t));
    broker_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    mesh_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    apply_configs = xQueueCreate(CONTROL_COUNT, sizeof(control_t));
    if (!local_data || !local_registration || !root_data || !root_registration ||
        !broker_configs || !mesh_configs || !apply_configs) return ESP_ERR_NO_MEM;
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
