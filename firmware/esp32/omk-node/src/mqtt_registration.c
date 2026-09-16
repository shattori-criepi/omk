#include "mqtt_registration.h"

#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif_ip_addr.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "nvs.h"
#include "node_registration.h"
#include "node_identity.h"
#include "node_protocol.h"
#include "mesh_network.h"
#include "sen66_sensor.h"

#define OMK_MQTT_BROKER_URI "mqtt://192.168.50.1:1883"
#define OMK_MQTT_TOPIC_SIZE 80
#define OMK_MQTT_PAYLOAD_SIZE 128
/* Two 31-byte advertisement fragments rendered as hex plus fixed JSON fields. */
#define OMK_MQTT_BLE_RELAY_PAYLOAD_SIZE 512
#define OMK_MQTT_SEN66_PAYLOAD_SIZE 768
#define OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE 1152
/* Includes a maximum-length logical ID and both uint32 SEN66 diagnostics. */
#define OMK_REGISTRATION_PAYLOAD_SIZE 256
#define OMK_MQTT_CLIENT_ID_SIZE 32
static const char *TAG = "omk-mqtt";
static esp_mqtt_client_handle_t client;
static bool client_started;
static volatile bool client_connected;
static uint32_t mqtt_disconnect_count;
static int64_t mqtt_last_connected_us;
static int64_t mqtt_disconnected_since_us;
static bool ip_handler_registered;
static char registration_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_config_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_ack_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_payload[OMK_REGISTRATION_PAYLOAD_SIZE];
static char client_id[OMK_MQTT_CLIENT_ID_SIZE];
static char device_status_topic[OMK_MQTT_TOPIC_SIZE];
static char device_status_logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
static char device_status_will_payload[OMK_MQTT_PAYLOAD_SIZE];
static bool device_status_configured;
static bool sen66_connected;
static bool sen66_diagnostics_available;
static uint32_t sen66_recovery_count;
static uint32_t sen66_measurement_timeout_count;

static bool logical_id_is_valid(const char *logical_id);

static bool is_lower_hex_identifier(const char *value, size_t length) {
    if (value == NULL || strlen(value) != length) {
        return false;
    }
    for (size_t index = 0; index < length; ++index) {
        char character = value[index];
        if (!((character >= '0' && character <= '9') ||
              (character >= 'a' && character <= 'f'))) {
            return false;
        }
    }
    return true;
}

static bool registration_is_persisted(void) {
    uint8_t state;
    esp_err_t err = node_registration_get_provisioning_state(false, &state);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Could not read registration state: %s", esp_err_to_name(err));
        return false;
    }
    return state == OMK_NODE_PROVISIONING_STATE_REGISTERED;
}

static void publish_registration_status(void) {
    bool registered = registration_is_persisted();
    const char *state = registered ? "registered" : "provisioned";
    char logical_id_field[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 20] = {0};
    if (registered) {
        char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
        esp_err_t err = node_registration_get_logical_id(logical_id, sizeof(logical_id));
        if (err != ESP_OK || !logical_id_is_valid(logical_id)) {
            ESP_LOGW(TAG, "Could not read a valid persisted logical ID for registration status");
            return;
        }
        /* Validation excludes JSON metacharacters; never publish an invalid ID. */
        int id_written = snprintf(logical_id_field, sizeof(logical_id_field),
                                  ",\"logical_id\":\"%s\"", logical_id);
        if (id_written < 0 || id_written >= (int)sizeof(logical_id_field)) {
            return;
        }
    }
    char sen66_diagnostic_fields[96] = {0};
    if (sen66_diagnostics_available) {
        int diagnostic_written = snprintf(
            sen66_diagnostic_fields, sizeof(sen66_diagnostic_fields),
            ",\"sen66_rc\":%" PRIu32 ",\"sen66_to\":%" PRIu32,
            sen66_recovery_count, sen66_measurement_timeout_count);
        if (diagnostic_written < 0 || diagnostic_written >= (int)sizeof(sen66_diagnostic_fields)) {
            ESP_LOGE(TAG, "Could not build SEN66 diagnostic status fields");
            return;
        }
    }
    int written = snprintf(registration_payload, sizeof(registration_payload),
                           "{\"protocol_version\":%u,\"node_id\":\"%s\","
                           "\"registration_state\":\"%s\"%s,\"capabilities\":%u,"
                           "\"connected_sensors\":%s%s}",
                           OMK_NODE_PROTOCOL_VERSION, client_id + strlen("omk-node-"),
                           state, logical_id_field, OMK_NODE_CAPABILITIES,
                           sen66_connected ? "[\"sen66\"]" : "[]", sen66_diagnostic_fields);
    if (written < 0 || written >= (int)sizeof(registration_payload)) {
        ESP_LOGE(TAG, "Could not build registration status payload");
        return;
    }
    int message_id = esp_mqtt_client_publish(client, registration_topic,
                                             registration_payload, 0, 1, 1);
    if (message_id < 0) {
        ESP_LOGW(TAG, "Could not queue retained registration status: %d", message_id);
        return;
    }
    ESP_LOGI(TAG, "Published retained registration status (message_id=%d)",
             message_id);
}

static bool logical_id_is_valid(const char *logical_id) {
    size_t length = strlen(logical_id);
    if (length == 0 || length > OMK_NODE_LOGICAL_ID_MAX_LENGTH) {
        return false;
    }
    for (size_t i = 0; i < length; ++i) {
        char character = logical_id[i];
        bool allowed = (character >= 'a' && character <= 'z') ||
                       (character >= 'A' && character <= 'Z') ||
                       (character >= '0' && character <= '9') ||
                       character == '-' || character == '_';
        if (!allowed) {
            return false;
        }
    }
    return true;
}

/* ``omk/<logical_id>/status`` is the retained availability of the logical
 * device, not an individual sensor-reading quality.  Freshness remains based
 * on the timestamp of the actual measurement. */
static bool configure_device_status(const char *logical_id) {
    int written = snprintf(device_status_logical_id, sizeof(device_status_logical_id), "%s", logical_id);
    if (written < 0 || written >= (int)sizeof(device_status_logical_id)) {
        return false;
    }
    written = snprintf(device_status_topic, sizeof(device_status_topic),
                       "omk/%s/status", logical_id);
    if (written < 0 || written >= (int)sizeof(device_status_topic)) {
        return false;
    }
    written = snprintf(device_status_will_payload, sizeof(device_status_will_payload),
                       "{\"device_id\":\"%s\",\"status\":\"offline\"}", logical_id);
    if (written < 0 || written >= (int)sizeof(device_status_will_payload)) {
        return false;
    }
    device_status_configured = true;
    return true;
}

static void load_persisted_device_status(void) {
    char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    esp_err_t err = node_registration_get_logical_id(logical_id, sizeof(logical_id));
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return;
    }
    if (err != ESP_OK || !logical_id_is_valid(logical_id) || !configure_device_status(logical_id)) {
        ESP_LOGW(TAG, "Could not configure logical-device MQTT status: %s", esp_err_to_name(err));
    }
}

static void publish_device_status(const char *status) {
    if (!device_status_configured) {
        return;
    }
    char payload[OMK_MQTT_PAYLOAD_SIZE];
    int written = snprintf(payload, sizeof(payload),
                           "{\"device_id\":\"%s\",\"status\":\"%s\"}",
                           device_status_logical_id, status);
    if (written < 0 || written >= (int)sizeof(payload)) {
        ESP_LOGE(TAG, "Could not build logical-device status payload");
        return;
    }
    int message_id = esp_mqtt_client_publish(client, device_status_topic, payload, 0, 0, 1);
    if (message_id < 0) {
        ESP_LOGW(TAG, "Could not queue retained logical-device status: %d", message_id);
    }
}

static void publish_registration_ack(const char *logical_id) {
    char payload[OMK_MQTT_PAYLOAD_SIZE];
    int written = snprintf(payload, sizeof(payload),
                           "{\"protocol_version\":%u,\"node_id\":\"%s\","
                           "\"logical_id\":\"%s\",\"registration_state\":\"registered\"}",
                           OMK_NODE_PROTOCOL_VERSION, client_id + strlen("omk-node-"),
                           logical_id);
    if (written < 0 || written >= (int)sizeof(payload)) {
        ESP_LOGE(TAG, "Could not build registration ACK payload");
        return;
    }
    int message_id = esp_mqtt_client_publish(client, registration_ack_topic,
                                             payload, 0, 1, 1);
    if (message_id < 0) {
        ESP_LOGW(TAG, "Could not queue retained registration ACK: %d", message_id);
        return;
    }
    ESP_LOGI(TAG, "Published retained registration ACK (message_id=%d)", message_id);
}

static void clear_retained_registration_ack(void) {
    if (esp_mqtt_client_publish(client, registration_ack_topic, "", 0, 1, 1) < 0) {
        ESP_LOGW(TAG, "Could not clear retained registration ACK");
    }
}

static void clear_retained_registration_config(void) {
    if (esp_mqtt_client_publish(client, registration_config_topic, "", 0, 1, 1) < 0) {
        ESP_LOGW(TAG, "Could not clear retained registration config");
    }
}

static void process_registration_config(const esp_mqtt_event_handle_t event) {
    if (event->topic == NULL || event->topic_len != (int)strlen(registration_config_topic) ||
        memcmp(event->topic, registration_config_topic, event->topic_len) != 0) {
        ESP_LOGW(TAG, "Ignoring registration config for a different topic");
        return;
    }
    if (event->current_data_offset != 0 || event->data_len != event->total_data_len) {
        ESP_LOGW(TAG, "Ignoring fragmented registration config");
        return;
    }

    cJSON *root = cJSON_ParseWithLength(event->data, event->data_len);
    if (root == NULL) {
        ESP_LOGW(TAG, "Ignoring invalid registration config JSON");
        return;
    }
    const cJSON *protocol_version = cJSON_GetObjectItemCaseSensitive(root, "protocol_version");
    const cJSON *logical_id = cJSON_GetObjectItemCaseSensitive(root, "logical_id");
    bool valid = cJSON_IsNumber(protocol_version) &&
                 protocol_version->valueint == OMK_NODE_PROTOCOL_VERSION;
    if (!valid) {
        ESP_LOGW(TAG, "Ignoring invalid registration config fields");
        cJSON_Delete(root);
        return;
    }
    if (cJSON_IsNull(logical_id)) {
        esp_err_t clear_err = node_registration_clear();
        if (clear_err == ESP_OK) {
            if (device_status_configured) {
                esp_mqtt_client_publish(client, device_status_topic, "", 0, 0, 1);
                device_status_configured = false;
            }
            clear_retained_registration_ack();
            clear_retained_registration_config();
            publish_registration_status();
            ESP_LOGI(TAG, "Cleared Node logical registration; Wi-Fi provisioning retained");
        } else {
            ESP_LOGE(TAG, "Could not clear Node registration: %s", esp_err_to_name(clear_err));
        }
        cJSON_Delete(root);
        return;
    }
    valid = cJSON_IsString(logical_id) && logical_id->valuestring != NULL &&
            logical_id_is_valid(logical_id->valuestring);
    if (!valid) {
        ESP_LOGW(TAG, "Ignoring invalid registration config fields");
        cJSON_Delete(root);
        return;
    }
    esp_err_t save_err = node_registration_save(logical_id->valuestring);
    if (save_err == ESP_OK) {
        if (!configure_device_status(logical_id->valuestring)) {
            ESP_LOGE(TAG, "Could not configure logical-device MQTT status after registration");
        } else {
            publish_device_status("online");
        }
        publish_registration_ack(logical_id->valuestring);
        publish_registration_status();
    } else {
        ESP_LOGE(TAG, "Could not persist registration: %s", esp_err_to_name(save_err));
    }
    cJSON_Delete(root);
}

static void mqtt_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    (void)arg;
    (void)event_base;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        client_connected = true;
        mqtt_last_connected_us = esp_timer_get_time();
        mqtt_disconnected_since_us = 0;
        mesh_network_log_diagnostics("MQTT connected", 0);
        ESP_LOGI(TAG, "MQTT connected");
        if (esp_mqtt_client_subscribe(client, registration_config_topic, 1) < 0) {
            ESP_LOGW(TAG, "Could not subscribe to registration config");
        }
        publish_registration_status();
        publish_device_status("online");
        break;
    case MQTT_EVENT_DATA:
        process_registration_config(event_data);
        break;
    case MQTT_EVENT_DISCONNECTED:
        client_connected = false;
        if (mqtt_disconnected_since_us == 0) mqtt_disconnected_since_us = esp_timer_get_time();
        mqtt_disconnect_count++;
        mesh_network_log_diagnostics("MQTT disconnected", 0);
        ESP_LOGW(TAG, "MQTT disconnected: count=%" PRIu32 "; automatic reconnect pending",
                 mqtt_disconnect_count);
        break;
    case MQTT_EVENT_ERROR: {
        const esp_mqtt_event_handle_t event = event_data;
        if (event != NULL && event->error_handle != NULL) {
            ESP_LOGW(TAG, "MQTT_EVENT_ERROR: type=%d tls=%d stack=%d socket_errno=%d connect_rc=%d",
                     event->error_handle->error_type,
                     event->error_handle->esp_tls_last_esp_err,
                     event->error_handle->esp_tls_stack_err,
                     event->error_handle->esp_transport_sock_errno,
                     event->error_handle->connect_return_code);
        } else {
            ESP_LOGW(TAG, "MQTT_EVENT_ERROR without error details");
        }
        break;
    }
    default:
        break;
    }
}

uint32_t mqtt_registration_get_disconnect_count(void) {
    return mqtt_disconnect_count;
}

bool mqtt_registration_is_connected(void) { return client_connected; }

bool mqtt_registration_is_started(void) { return client_started; }

uint32_t mqtt_registration_get_last_connected_uptime_s(void) {
    return mqtt_last_connected_us > 0 ? (uint32_t)(mqtt_last_connected_us / 1000000) : 0;
}

uint32_t mqtt_registration_get_disconnected_duration_s(void) {
    if (client_connected || !client_started) return 0;
    int64_t now_us = esp_timer_get_time();
    if (mqtt_disconnected_since_us == 0) mqtt_disconnected_since_us = now_us;
    return (uint32_t)((now_us - mqtt_disconnected_since_us) / 1000000);
}

void mqtt_registration_set_sen66_connected(bool connected) {
    if (sen66_connected == connected) {
        return;
    }
    sen66_connected = connected;
    if (client != NULL && client_connected) {
        publish_registration_status();
    }
}

void mqtt_registration_set_sen66_diagnostics(uint32_t recovery_count,
                                             uint32_t measurement_timeout_count) {
    sen66_diagnostics_available = true;
    sen66_recovery_count = recovery_count;
    sen66_measurement_timeout_count = measurement_timeout_count;
    if (client != NULL && client_connected) {
        publish_registration_status();
    }
}

esp_err_t mqtt_registration_publish_mesh_status(const char *payload) {
    if (payload == NULL || payload[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (client == NULL || !client_connected) {
        return ESP_ERR_INVALID_STATE;
    }
    uint64_t node_id;
    esp_err_t err = node_identity_get_id(&node_id);
    if (err != ESP_OK) {
        return err;
    }
    char topic[OMK_MQTT_TOPIC_SIZE];
    int written = snprintf(topic, sizeof(topic), "omk/node/%012" PRIx64 "/status", node_id);
    if (written < 0 || written >= (int)sizeof(topic) ||
        strlen(payload) >= OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE) {
        return ESP_ERR_INVALID_SIZE;
    }
    int message_id = esp_mqtt_client_enqueue(client, topic, payload, 0, 0, 0, true);
    return message_id < 0 ? ESP_FAIL : ESP_OK;
}

static void start_or_reconnect_mqtt(void) {
    if (client_started) {
        ESP_LOGI(TAG, "Requesting MQTT reconnect after IP acquisition");
        mesh_network_log_diagnostics("MQTT reconnect requested", 0);
        esp_err_t err = esp_mqtt_client_reconnect(client);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "MQTT reconnect request failed: %s", esp_err_to_name(err));
        } else {
            ESP_LOGI(TAG, "MQTT reconnect requested");
        }
        return;
    }

    ESP_LOGI(TAG, "Starting MQTT client broker=%s", OMK_MQTT_BROKER_URI);
    esp_err_t err = esp_mqtt_client_start(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "MQTT start failed: %s", esp_err_to_name(err));
        return;
    }
    client_started = true;
    ESP_LOGI(TAG, "MQTT start requested after IP acquisition");
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                             int32_t event_id, void *event_data) {
    (void)arg;
    (void)event_base;
    (void)event_id;
    const ip_event_got_ip_t *event = event_data;
    if (event != NULL) {
        ESP_LOGI(TAG, "STA IP acquired for MQTT: ip=" IPSTR " gw=" IPSTR " mask=" IPSTR,
                 IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.gw),
                 IP2STR(&event->ip_info.netmask));
    } else {
        ESP_LOGW(TAG, "MQTT observed IP_EVENT_STA_GOT_IP without event data");
    }
    start_or_reconnect_mqtt();
}

esp_err_t mqtt_registration_start(void) {
    ESP_LOGI(TAG, "Initializing MQTT registration");
    if (ip_handler_registered) {
        return ESP_ERR_INVALID_STATE;
    }

    uint64_t node_id;
    esp_err_t err = node_identity_get_id(&node_id);
    if (err != ESP_OK) {
        return err;
    }

    int written = snprintf(registration_topic, sizeof(registration_topic),
                           "omk/node/%012" PRIx64 "/registration/status",
                           node_id);
    if (written < 0 || written >= (int)sizeof(registration_topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(registration_config_topic, sizeof(registration_config_topic),
                       "omk/node/%012" PRIx64 "/registration/config", node_id);
    if (written < 0 || written >= (int)sizeof(registration_config_topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(registration_ack_topic, sizeof(registration_ack_topic),
                       "omk/node/%012" PRIx64 "/registration/ack", node_id);
    if (written < 0 || written >= (int)sizeof(registration_ack_topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(client_id, sizeof(client_id), "omk-node-%012" PRIx64,
                       node_id);
    if (written < 0 || written >= (int)sizeof(client_id)) {
        return ESP_ERR_INVALID_SIZE;
    }

    load_persisted_device_status();
    const esp_mqtt_client_config_t config = {
        .broker.address.uri = OMK_MQTT_BROKER_URI,
        .credentials.client_id = client_id,
        .session.last_will = {
            .topic = device_status_configured ? device_status_topic : NULL,
            .msg = device_status_configured ? device_status_will_payload : NULL,
            .qos = 0,
            .retain = device_status_configured ? 1 : 0,
        },
    };
    client = esp_mqtt_client_init(&config);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "MQTT client created broker=%s", OMK_MQTT_BROKER_URI);

    err = esp_mqtt_client_register_event(client, MQTT_EVENT_ANY,
                                         mqtt_event_handler, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                     ip_event_handler, NULL);
    if (err != ESP_OK) {
        return err;
    }
    ip_handler_registered = true;

    /* Covers the small interval where Wi-Fi obtains an IP before this module
     * registers its independent event handler. */
    wifi_ap_record_t ap_info;
    if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
        ESP_LOGI(TAG, "Existing STA association found while starting MQTT");
        start_or_reconnect_mqtt();
    }
    return ESP_OK;
}

esp_err_t mqtt_registration_publish_environment(const char *sensor_id,
                                                float temperature_c,
                                                uint8_t relative_humidity_percent) {
    if (sensor_id == NULL || sensor_id[0] == '\0' ||
        relative_humidity_percent > 100 || temperature_c < -20.0f ||
        temperature_c > 60.0f) {
        return ESP_ERR_INVALID_ARG;
    }
    if (client == NULL || !client_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    char topic[OMK_MQTT_TOPIC_SIZE];
    char payload[OMK_MQTT_PAYLOAD_SIZE];
    int written = snprintf(topic, sizeof(topic), "omk/%s/environment", sensor_id);
    if (written < 0 || written >= (int)sizeof(topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(payload, sizeof(payload),
                       "{\"device_id\":\"%s\",\"quality\":\"normal\","
                       "\"temperature_c\":%.1f,\"relative_humidity_percent\":%u}",
                       sensor_id, (double)temperature_c,
                       relative_humidity_percent);
    if (written < 0 || written >= (int)sizeof(payload)) {
        return ESP_ERR_INVALID_SIZE;
    }

    /* This may be called from the Bluedroid callback task. Enqueueing hands
     * the message to ESP-MQTT's own task without blocking BLE scanning. */
    int message_id = esp_mqtt_client_enqueue(client, topic, payload, 0, 0, 0, true);
    return message_id < 0 ? ESP_FAIL : ESP_OK;
}

esp_err_t mqtt_registration_publish_ble_relay(const char *relay_node_id, const char *ble_address, int rssi,
                                              const uint8_t *manufacturer_data, size_t manufacturer_length,
                                              const uint8_t *service_data, size_t service_length) {
    if (!is_lower_hex_identifier(relay_node_id, OMK_NODE_ID_HEX_LENGTH) ||
        !is_lower_hex_identifier(ble_address, 12) || rssi < -127 || rssi > 20 ||
        manufacturer_length > 31 || service_length > 31 ||
        (manufacturer_length > 0 && manufacturer_data == NULL) ||
        (service_length > 0 && service_data == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (client == NULL || !client_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    char topic[OMK_MQTT_TOPIC_SIZE];
    char payload[OMK_MQTT_BLE_RELAY_PAYLOAD_SIZE];
    int written = snprintf(topic, sizeof(topic), "omk-relay/%s/ble/raw", relay_node_id);
    if (written < 0 || written >= (int)sizeof(topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    size_t offset = 0;
    written = snprintf(payload, sizeof(payload),
                       "{\"protocol_version\":1,\"relay_node_id\":\"%s\",\"ble_address\":\"%s\",\"rssi\":%d,\"manufacturer_data\":[",
                       relay_node_id, ble_address, rssi);
    if (written < 0 || written >= (int)sizeof(payload)) return ESP_ERR_INVALID_SIZE;
    offset = (size_t)written;
    if (manufacturer_length > 0) {
        written = snprintf(payload + offset, sizeof(payload) - offset, "{\"company_id\":2409,\"data\":\"");
        if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
        offset += (size_t)written;
        for (size_t i = 0; i < manufacturer_length; ++i) {
            written = snprintf(payload + offset, sizeof(payload) - offset, "%02x", manufacturer_data[i]);
            if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
            offset += (size_t)written;
        }
        written = snprintf(payload + offset, sizeof(payload) - offset, "\"}");
        if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
        offset += (size_t)written;
    }
    written = snprintf(payload + offset, sizeof(payload) - offset, "],\"service_data\":[");
    if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
    offset += (size_t)written;
    if (service_length > 0) {
        written = snprintf(payload + offset, sizeof(payload) - offset, "{\"uuid\":\"0000fd3d-0000-1000-8000-00805f9b34fb\",\"data\":\"");
        if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
        offset += (size_t)written;
        for (size_t i = 0; i < service_length; ++i) {
            written = snprintf(payload + offset, sizeof(payload) - offset, "%02x", service_data[i]);
            if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
            offset += (size_t)written;
        }
        written = snprintf(payload + offset, sizeof(payload) - offset, "\"}");
        if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;
        offset += (size_t)written;
    }
    written = snprintf(payload + offset, sizeof(payload) - offset, "]}");
    if (written < 0 || (size_t)written >= sizeof(payload) - offset) return ESP_ERR_INVALID_SIZE;

    int message_id = esp_mqtt_client_enqueue(client, topic, payload, 0, 0, 0, true);
    return message_id < 0 ? ESP_FAIL : ESP_OK;
}

static bool append_text(char *buffer, size_t size, size_t *offset, const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    int written = vsnprintf(buffer + *offset, size - *offset, format, arguments);
    va_end(arguments);
    if (written < 0 || (size_t)written >= size - *offset) {
        return false;
    }
    *offset += (size_t)written;
    return true;
}

static bool append_json_float(char *buffer, size_t size, size_t *offset,
                              const char *name, float value, bool trailing_comma) {
    if (!append_text(buffer, size, offset, "\"%s\":", name)) {
        return false;
    }
    if (!isfinite(value)) {
        return append_text(buffer, size, offset, "%s", trailing_comma ? "null," : "null");
    }
    return append_text(buffer, size, offset, trailing_comma ? "%.2f," : "%.2f", (double)value);
}

esp_err_t mqtt_registration_publish_sen66(const char *sensor_id,
                                          const sen66_measurement_t *measurement) {
    if (sensor_id == NULL || sensor_id[0] == '\0' || measurement == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (client == NULL || !client_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    char topic[OMK_MQTT_TOPIC_SIZE];
    char payload[OMK_MQTT_SEN66_PAYLOAD_SIZE];
    int written = snprintf(topic, sizeof(topic), "omk/%s/sen66", sensor_id);
    if (written < 0 || written >= (int)sizeof(topic)) {
        return ESP_ERR_INVALID_SIZE;
    }

    size_t offset = 0;
    bool complete =
        append_text(payload, sizeof(payload), &offset,
                    "{\"device_id\":\"%s\",\"uptime_ms\":%" PRIu32 ",",
                    sensor_id, measurement->uptime_ms) &&
        append_json_float(payload, sizeof(payload), &offset, "pm1_0_ug_m3",
                          measurement->pm1_0_ug_m3, true) &&
        append_json_float(payload, sizeof(payload), &offset, "pm2_5_ug_m3",
                          measurement->pm2_5_ug_m3, true) &&
        append_json_float(payload, sizeof(payload), &offset, "pm4_0_ug_m3",
                          measurement->pm4_0_ug_m3, true) &&
        append_json_float(payload, sizeof(payload), &offset, "pm10_0_ug_m3",
                          measurement->pm10_0_ug_m3, true) &&
        append_json_float(payload, sizeof(payload), &offset, "relative_humidity_percent",
                          measurement->relative_humidity_percent, true) &&
        append_json_float(payload, sizeof(payload), &offset, "temperature_celsius",
                          measurement->temperature_celsius, true) &&
        append_json_float(payload, sizeof(payload), &offset, "voc_index",
                          measurement->voc_index, true) &&
        append_json_float(payload, sizeof(payload), &offset, "nox_index",
                          measurement->nox_index, true) &&
        append_json_float(payload, sizeof(payload), &offset, "co2_ppm",
                          measurement->co2_ppm, false) &&
        append_text(payload, sizeof(payload), &offset, "}");
    if (!complete) {
        return ESP_ERR_INVALID_SIZE;
    }

    int message_id = esp_mqtt_client_enqueue(client, topic, payload, 0, 0, 0, true);
    return message_id < 0 ? ESP_FAIL : ESP_OK;
}
