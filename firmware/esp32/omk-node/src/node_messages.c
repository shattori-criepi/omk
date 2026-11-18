#include "mesh_diag.h"
#include "node_messages.h"
#include "ble_scan_runtime.h"
#include "discovery_ble.h"

#include <inttypes.h>
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mesh_transport.h"
#include "node_identity.h"
#include "node_protocol.h"
#include "node_registration.h"
#include "sen66_sensor.h"

static const char *TAG = "node_messages";
static char identity[13], boot_id[17];
static esp_reset_reason_t reset_reason;
static TaskHandle_t status_task;
static atomic_bool ready;
/* Config apply/drop and record snapshot/enqueue share one owner. Publishers
 * only try this mutex: neither BLE callbacks nor sensor reads wait for NVS. */
static StaticSemaphore_t message_mutex_storage;
static SemaphoreHandle_t message_mutex;
static portMUX_TYPE state_lock = portMUX_INITIALIZER_UNLOCKED;
static bool sen66_connected;
static uint32_t sensor_recoveries, sensor_timeouts, status_seq, ble_seq, enqueue_seq;
/* Accessed only under message_mutex. Formatting is not a transport failure. */
static uint16_t format_errors, send_errors;
static void count_message_error(uint16_t *counter) { if (*counter < UINT16_MAX) ++*counter; }

static bool publisher_enter(void) {
    return atomic_load(&ready) && xSemaphoreTake(message_mutex, 0) == pdTRUE;
}
static bool hex_identity(const char *id) {
    if (!id || strlen(id) != 12) return false;
    for (const char *p = id; *p; ++p)
        if (!((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f'))) return false;
    return true;
}
static bool append_item(cJSON *array, cJSON *item) {
    if (!item) return false;
    if (cJSON_AddItemToArray(array, item)) return true;
    cJSON_Delete(item);
    return false;
}
static cJSON *record(uint32_t sequence, uint32_t revision) {
    cJSON *root = cJSON_CreateObject();
    if (!root) return NULL;
    if (!cJSON_AddNumberToObject(root, "protocol_version", 2) ||
        !cJSON_AddStringToObject(root, "node_id", identity) ||
        !cJSON_AddStringToObject(root, "boot_id", boot_id) ||
        !cJSON_AddNumberToObject(root, "seq", sequence) ||
        !cJSON_AddNumberToObject(root, "revision", revision)) {
        cJSON_Delete(root); return NULL;
    }
    return root;
}
static esp_err_t publish(mesh_message_kind_t kind, cJSON *root) {
    if (!root) { count_message_error(&format_errors); return ESP_ERR_NO_MEM; }
    char payload[MESH_TRANSPORT_PAYLOAD_MAX + 1];
    bool formatted = cJSON_PrintPreallocated(root, payload, sizeof(payload), false);
    cJSON_Delete(root);
    if (!formatted) { count_message_error(&format_errors); return ESP_ERR_INVALID_SIZE; }
    esp_err_t error = mesh_transport_publish(kind, payload, strlen(payload));
    if (error != ESP_OK) count_message_error(&send_errors);
    return error;
}
static cJSON *make_state_record(const char *result, bool diagnostic) {
    char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    uint32_t revision = 0;
    esp_err_t registration_error = node_registration_snapshot(logical_id, sizeof(logical_id), &revision);
    if (registration_error == ESP_OK && logical_id[0] && !node_logical_id_valid(logical_id))
        registration_error = ESP_ERR_INVALID_ARG;
    if (registration_error != ESP_OK) {
        if (!diagnostic) return NULL; /* Never claim a persisted registration/ACK. */
        logical_id[0] = 0;
        revision = 0;
    }
    bool connected;
    uint32_t recoveries, timeouts;
    portENTER_CRITICAL(&state_lock);
    connected = sen66_connected; recoveries = sensor_recoveries; timeouts = sensor_timeouts;
    portEXIT_CRITICAL(&state_lock);
    cJSON *root = record(++status_seq, revision);
    if (!root) return NULL;
    bool complete = cJSON_AddStringToObject(root, "registration_state", logical_id[0] ? "registered" : "provisioned") &&
        (logical_id[0] ? cJSON_AddStringToObject(root, "logical_id", logical_id) : cJSON_AddNullToObject(root, "logical_id")) &&
        cJSON_AddNumberToObject(root, "capabilities", OMK_NODE_CAPABILITIES);
    cJSON *sensors = complete ? cJSON_AddArrayToObject(root, "connected_sensors") : NULL;
    complete = complete && sensors && (!connected || append_item(sensors, cJSON_CreateString("sen66"))) &&
        cJSON_AddNumberToObject(root, "uptime_s", esp_timer_get_time() / 1000000) &&
        cJSON_AddNumberToObject(root, "sen66_rc", recoveries) &&
        cJSON_AddNumberToObject(root, "sen66_to", timeouts) &&
        (!result || cJSON_AddStringToObject(root, "result", result)) &&
        (!diagnostic || cJSON_AddNumberToObject(root, "reg_err", registration_error));
    if (!complete) { cJSON_Delete(root); return NULL; }
    return root;
}
static cJSON *state_record(const char *result) { return make_state_record(result, false); }
static bool add_mac(cJSON *root, const char *key, const uint8_t mac[6]) {
    const uint8_t unknown[6] = {0};
    if (!memcmp(mac, unknown, sizeof(unknown))) return cJSON_AddNullToObject(root, key) != NULL;
    char text[18];
    snprintf(text, sizeof(text), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return cJSON_AddStringToObject(root, key, text) != NULL;
}
static cJSON *heartbeat_record(void) {
    cJSON *root = make_state_record(NULL, true);
    if (!root) return NULL;
    mesh_transport_status_t mesh;
    mesh_transport_get_status(&mesh);
    /* Only the existing non-retained heartbeat carries diagnostics. Retained
     * registration and ACK records keep their schema and revision semantics.
     * This is a local snapshot, not a delivery ACK or a recovery trigger. */
    bool complete =
        cJSON_AddNumberToObject(root, "mesh_layer", mesh.layer) &&
        cJSON_AddBoolToObject(root, "is_root", mesh.is_root) &&
        add_mac(root, "parent_bssid", mesh.parent_mac) &&
        add_mac(root, "root_mac", mesh.root_mac) &&
        cJSON_AddBoolToObject(root, "rootless", mesh.rootless) &&
        cJSON_AddBoolToObject(root, "has_ip", mesh.has_ip) &&
        cJSON_AddBoolToObject(root, "mqtt_connected", mesh.mqtt_connected) &&
        cJSON_AddNumberToObject(root, "reset_reason", reset_reason) &&
        cJSON_AddNumberToObject(root, "parent_disconnect_count", mesh.parent_disconnect_count) &&
        cJSON_AddNumberToObject(root, "root_change_count", mesh.root_change_count) &&
        (mesh.last_disconnect_valid ?
            cJSON_AddNumberToObject(root, "last_disconnect_reason", mesh.last_disconnect_reason) :
            cJSON_AddNullToObject(root, "last_disconnect_reason")) &&
        (mesh.last_disconnect_valid ?
            cJSON_AddNumberToObject(root, "last_disconnect_rssi", mesh.last_disconnect_rssi) :
            cJSON_AddNullToObject(root, "last_disconnect_rssi")) &&
        cJSON_AddNumberToObject(root, "tx_dropped", mesh.tx_dropped) &&
        cJSON_AddNumberToObject(root, "mesh_tx_ok", mesh.mesh_tx_ok) &&
        cJSON_AddNumberToObject(root, "mesh_tx_errors", mesh.mesh_tx_errors) &&
        cJSON_AddNumberToObject(root, "mesh_rx_ok", mesh.mesh_rx_ok) &&
        cJSON_AddNumberToObject(root, "fmt_err", format_errors) &&
        cJSON_AddNumberToObject(root, "send_err", send_errors);
    cJSON *recovery = complete ? cJSON_AddObjectToObject(root, "recovery") : NULL;
    complete = recovery &&
        cJSON_AddNumberToObject(recovery, "action", mesh.recovery_action) &&
        cJSON_AddNumberToObject(recovery, "req", mesh.recovery_requests) &&
        cJSON_AddNumberToObject(recovery, "ok", mesh.recovery_ok) &&
        cJSON_AddNumberToObject(recovery, "err", mesh.recovery_error) &&
        cJSON_AddNumberToObject(recovery, "link", mesh.recovery_link) &&
        cJSON_AddNumberToObject(recovery, "ack", mesh.mqtt_pubacks) &&
        cJSON_AddNumberToObject(recovery, "vote", mesh.recovery_votes) &&
        cJSON_AddNumberToObject(recovery, "demote", mesh.recovery_demotions);
    if (!complete) { cJSON_Delete(root); return NULL; }
    return root;
}
static void send_state(void) {
    if (!publisher_enter()) return;
    /* Non-retained state is the heartbeat; retained registration is never liveness. */
    publish(MESH_MESSAGE_NODE_STATUS, heartbeat_record());
    publish(MESH_MESSAGE_REGISTRATION_STATUS, state_record(NULL));
    xSemaphoreGive(message_mutex);
}
static void send_scan_report(void) {
    static uint8_t binary[AB_RECORD_SIZE];
    static char payload[AB_JSON_MAX+1];
    static uint32_t sequence;
    ab_runtime_reported();
    if (!publisher_enter()) { ab_runtime_drop(); return; }
    uint32_t revision=node_registration_get_revision();
    ab_runtime_pack(binary);
    size_t length=ab_report_json(binary,identity,boot_id,++sequence,revision,payload,sizeof(payload));
    if(length) mesh_transport_publish(MESH_MESSAGE_MEASUREMENT,payload,length);
    else ab_runtime_drop();
    xSemaphoreGive(message_mutex);
}
static void state_task(void *arg) {
    (void)arg;
    uint64_t status_due=0;
    /* Deterministic stagger avoids all four reports sharing a boundary. */
    uint64_t report_due=(uint64_t)esp_timer_get_time()/1000+60000+(unsigned char)identity[11]*97;
    bool notified=false;
    for (;;) {
        uint64_t now=(uint64_t)esp_timer_get_time()/1000;
        discovery_ble_control_tick();
        ab_event_t event;
        if(ab_runtime_event(&event)) mesh_diag_scan(event.ms,event.epoch,event.detail);
        if(notified || now>=status_due) { send_state(); status_due=now+30000; }
        if(now>=report_due) { if(ab_runtime_periodic_allowed()) send_scan_report(); report_due=now+60000; }
        else if(ab_runtime_report_due()) send_scan_report();
        notified=ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(100))!=0;
    }
}
esp_err_t node_messages_start(void) {
    /* Startup has a single main-task owner, before producers are started. */
    if (status_task) return ESP_OK;
    if (!message_mutex) message_mutex = xSemaphoreCreateMutexStatic(&message_mutex_storage);
    if (!message_mutex) return ESP_ERR_NO_MEM;
    if (!identity[0]) {
        esp_err_t err = node_identity_get_id_hex(identity, sizeof(identity));
        if (err != ESP_OK) { identity[0] = 0; return err; }
        if (!hex_identity(identity)) { identity[0] = 0; return ESP_ERR_INVALID_STATE; }
        uint64_t boot = ((uint64_t)esp_random() << 32) | esp_random();
        snprintf(boot_id, sizeof(boot_id), "%016" PRIx64, boot);
    }
    reset_reason = esp_reset_reason();
    mesh_diag_boot(identity, boot_id, reset_reason);
    ab_runtime_boot(identity,boot_id);
    atomic_store(&ready, true);
    if (xTaskCreate(state_task, "node_state", 4096, NULL, 4, &status_task) != pdPASS) {
        atomic_store(&ready, false); status_task = NULL; return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
void node_messages_handle_config(const char *payload, size_t length) {
    if (!atomic_load(&ready) || !payload || !length || length > MESH_TRANSPORT_PAYLOAD_MAX) return;
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(payload, length, &end, false);
    if (!root) return;
    while (end < payload + length && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')) ++end;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "protocol_version");
    const cJSON *rev = cJSON_GetObjectItemCaseSensitive(root, "revision");
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(root, "logical_id");
    bool valid = end == payload + length && cJSON_IsObject(root) && cJSON_GetArraySize(root) == 3 &&
        cJSON_IsNumber(version) && version->valuedouble == 2 &&
        cJSON_IsNumber(rev) && rev->valuedouble >= 1 && rev->valuedouble <= UINT32_MAX &&
        rev->valuedouble == (uint32_t)rev->valuedouble &&
        (cJSON_IsNull(id) || (cJSON_IsString(id) && node_logical_id_valid(id->valuestring)));
    if (valid && xSemaphoreTake(message_mutex, portMAX_DELAY) == pdTRUE) {
        uint32_t before = node_registration_get_revision();
        esp_err_t err = node_registration_apply((uint32_t)rev->valuedouble,
                                                cJSON_IsNull(id) ? NULL : id->valuestring);
        if (err == ESP_OK && node_registration_get_revision() != before)
            mesh_transport_drop_pending();
        /* Failure ACK contains actual persisted state, never a fabricated
         * requested revision. Same-revision repeats do not flush sensor data. */
        publish(MESH_MESSAGE_REGISTRATION_ACK, state_record(err == ESP_OK ? "applied" : "rejected"));
        xSemaphoreGive(message_mutex);
        if (err != ESP_OK) ESP_LOGW(TAG, "Registration not applied: %s", esp_err_to_name(err));
        if (status_task) xTaskNotifyGive(status_task);
    }
    cJSON_Delete(root);
}
void node_messages_set_sen66_connected(bool connected) {
    portENTER_CRITICAL(&state_lock); sen66_connected = connected; portEXIT_CRITICAL(&state_lock);
    if (status_task) xTaskNotifyGive(status_task);
}
void node_messages_set_sen66_diagnostics(uint32_t recoveries, uint32_t timeouts) {
    portENTER_CRITICAL(&state_lock); sensor_recoveries = recoveries; sensor_timeouts = timeouts; portEXIT_CRITICAL(&state_lock);
}
static bool add_value(cJSON *root, const char *key, float value) {
    return (isfinite(value) ? cJSON_AddNumberToObject(root, key, value) : cJSON_AddNullToObject(root, key)) != NULL;
}
static esp_err_t publish_sen66(const char *logical_id, const sen66_measurement_t *s) {
    char current[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    uint32_t revision;
    if (node_registration_snapshot(current, sizeof(current), &revision) != ESP_OK ||
        !current[0] || strcmp(current, logical_id)) return ESP_ERR_INVALID_STATE;
    cJSON *root = record(s->sequence, revision);
    if (!root) return ESP_ERR_NO_MEM;
    bool complete = cJSON_AddStringToObject(root, "device_id", logical_id) &&
        cJSON_AddNumberToObject(root, "uptime_ms", s->uptime_ms);
    cJSON *meta = complete ? cJSON_AddObjectToObject(root, "meta") : NULL;
    complete = complete && meta && cJSON_AddStringToObject(meta, "boot_id", boot_id) &&
        cJSON_AddNumberToObject(meta, "seq", s->sequence) &&
        cJSON_AddNumberToObject(meta, "enqueue_seq", enqueue_seq + 1) &&
        add_value(root, "pm1_0_ug_m3", s->pm1_0_ug_m3) &&
        add_value(root, "pm2_5_ug_m3", s->pm2_5_ug_m3) &&
        add_value(root, "pm4_0_ug_m3", s->pm4_0_ug_m3) &&
        add_value(root, "pm10_0_ug_m3", s->pm10_0_ug_m3) &&
        add_value(root, "relative_humidity_percent", s->relative_humidity_percent) &&
        add_value(root, "temperature_celsius", s->temperature_celsius) &&
        add_value(root, "voc_index", s->voc_index) && add_value(root, "nox_index", s->nox_index) &&
        add_value(root, "co2_ppm", s->co2_ppm);
    if (!complete) { cJSON_Delete(root); return ESP_ERR_NO_MEM; }
    esp_err_t err = publish(MESH_MESSAGE_SEN66, root);
    if (err == ESP_OK) ++enqueue_seq;
    return err;
}
esp_err_t node_messages_publish_sen66(const char *logical_id, const sen66_measurement_t *s) {
    if (!node_logical_id_valid(logical_id) || !s) return ESP_ERR_INVALID_ARG;
    if (!publisher_enter()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = publish_sen66(logical_id, s);
    xSemaphoreGive(message_mutex);
    return err;
}
static void hex_bytes(char *out, const uint8_t *data, size_t size) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < size; ++i) { out[i*2] = digits[data[i] >> 4]; out[i*2+1] = digits[data[i] & 15]; }
    out[size*2] = 0;
}
static cJSON *ble_record(const char *address, int rssi, const uint8_t *manufacturer,
                         size_t manufacturer_length, const uint8_t *service, size_t service_length) {
    cJSON *root = record(++ble_seq, node_registration_get_revision());
    if (!root) return NULL;
    char m[63], s[63]; hex_bytes(m, manufacturer, manufacturer_length); hex_bytes(s, service, service_length);
    bool complete = cJSON_AddStringToObject(root, "relay_node_id", identity) &&
        cJSON_AddStringToObject(root, "ble_address", address) && cJSON_AddNumberToObject(root, "rssi", rssi);
    cJSON *md = complete ? cJSON_AddArrayToObject(root, "manufacturer_data") : NULL;
    cJSON *sd = complete ? cJSON_AddArrayToObject(root, "service_data") : NULL;
    complete = complete && md && sd;
    if (complete && manufacturer_length) {
        cJSON *entry = cJSON_CreateObject();
        complete = entry && cJSON_AddNumberToObject(entry, "company_id", 2409) && cJSON_AddStringToObject(entry, "data", m);
        if (complete) complete = append_item(md, entry);
        else cJSON_Delete(entry);
    }
    if (complete && service_length) {
        cJSON *entry = cJSON_CreateObject();
        complete = entry && cJSON_AddStringToObject(entry, "uuid", "0000fd3d-0000-1000-8000-00805f9b34fb") &&
            cJSON_AddStringToObject(entry, "data", s);
        if (complete) complete = append_item(sd, entry);
        else cJSON_Delete(entry);
    }
    if (!complete) { cJSON_Delete(root); return NULL; }
    return root;
}
esp_err_t node_messages_publish_ble_relay(const char *relay_node_id, const char *address, int rssi,
    const uint8_t *manufacturer, size_t manufacturer_length, const uint8_t *service, size_t service_length) {
    if (!hex_identity(relay_node_id) || !hex_identity(address) || rssi < -127 || rssi > 20 ||
        manufacturer_length > 31 || service_length > 31 || (!manufacturer_length && !service_length) ||
        (manufacturer_length && !manufacturer) || (service_length && !service)) return ESP_ERR_INVALID_ARG;
    if (!publisher_enter()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = strcmp(relay_node_id, identity) ? ESP_ERR_INVALID_ARG :
        publish(MESH_MESSAGE_BLE_RAW, ble_record(address, rssi, manufacturer, manufacturer_length, service, service_length));
    xSemaphoreGive(message_mutex);
    return err;
}
