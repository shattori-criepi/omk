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
#include "esp_wifi.h"
#include "mqtt_client.h"
#include "node_registration.h"
#include "node_identity.h"
#include "node_protocol.h"
#include "sen66_sensor.h"

#define OMK_MQTT_BROKER_URI "mqtt://192.168.50.1:1883"
#define OMK_MQTT_TOPIC_SIZE 80
#define OMK_MQTT_PAYLOAD_SIZE 128
#define OMK_MQTT_RELAY_ENVIRONMENT_PAYLOAD_SIZE 256
#define OMK_MQTT_SEN66_PAYLOAD_SIZE 768
#define OMK_MQTT_CLIENT_ID_SIZE 32
static const char *TAG = "omk-mqtt";
static esp_mqtt_client_handle_t client;
static bool client_started;
static volatile bool client_connected;
static bool ip_handler_registered;
static char registration_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_config_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_ack_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_payload[OMK_MQTT_PAYLOAD_SIZE];
static char client_id[OMK_MQTT_CLIENT_ID_SIZE];

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

static bool relay_device_key_is_valid(const char *device_key) {
    static const char prefix[] = "switchbot:";
    return device_key != NULL && strncmp(device_key, prefix, sizeof(prefix) - 1) == 0 &&
           is_lower_hex_identifier(device_key + sizeof(prefix) - 1, 12);
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
    const char *state = registration_is_persisted() ? "registered" : "provisioned";
    int written = snprintf(registration_payload, sizeof(registration_payload),
                           "{\"protocol_version\":%u,\"node_id\":\"%s\","
                           "\"registration_state\":\"%s\",\"capabilities\":%u}",
                           OMK_NODE_PROTOCOL_VERSION, client_id + strlen("omk-node-"),
                           state, OMK_NODE_CAPABILITIES);
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
                 protocol_version->valueint == OMK_NODE_PROTOCOL_VERSION &&
                 cJSON_IsString(logical_id) && logical_id->valuestring != NULL &&
                 logical_id_is_valid(logical_id->valuestring);
    if (!valid) {
        ESP_LOGW(TAG, "Ignoring invalid registration config fields");
        cJSON_Delete(root);
        return;
    }
    esp_err_t save_err = node_registration_save(logical_id->valuestring);
    if (save_err == ESP_OK) {
        publish_registration_ack(logical_id->valuestring);
    } else {
        ESP_LOGE(TAG, "Could not persist registration: %s", esp_err_to_name(save_err));
    }
    cJSON_Delete(root);
}

static void mqtt_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    (void)arg;
    (void)event_base;
    (void)event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        client_connected = true;
        ESP_LOGI(TAG, "Connected to MQTT broker");
        if (esp_mqtt_client_subscribe(client, registration_config_topic, 1) < 0) {
            ESP_LOGW(TAG, "Could not subscribe to registration config");
        }
        publish_registration_status();
        break;
    case MQTT_EVENT_DATA:
        process_registration_config(event_data);
        break;
    case MQTT_EVENT_DISCONNECTED:
        client_connected = false;
        ESP_LOGW(TAG, "Disconnected from MQTT broker; automatic reconnect pending");
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "MQTT connection error");
        break;
    default:
        break;
    }
}

static void start_or_reconnect_mqtt(void) {
    if (client_started) {
        esp_err_t err = esp_mqtt_client_reconnect(client);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "MQTT reconnect request failed: %s", esp_err_to_name(err));
        }
        return;
    }

    esp_err_t err = esp_mqtt_client_start(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "MQTT start failed: %s", esp_err_to_name(err));
        return;
    }
    client_started = true;
    ESP_LOGI(TAG, "Starting MQTT after Wi-Fi IP acquisition");
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                             int32_t event_id, void *event_data) {
    (void)arg;
    (void)event_base;
    (void)event_id;
    (void)event_data;
    start_or_reconnect_mqtt();
}

esp_err_t mqtt_registration_start(void) {
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

    const esp_mqtt_client_config_t config = {
        .broker.address.uri = OMK_MQTT_BROKER_URI,
        .credentials.client_id = client_id,
    };
    client = esp_mqtt_client_init(&config);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }

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

esp_err_t mqtt_registration_publish_relay_environment(const char *device_key,
                                                       float temperature_c,
                                                       uint8_t relative_humidity_percent,
                                                       const char *relay_node_id) {
    if (!relay_device_key_is_valid(device_key) ||
        !is_lower_hex_identifier(relay_node_id, OMK_NODE_ID_HEX_LENGTH) ||
        relative_humidity_percent > 100 || temperature_c < -20.0f ||
        temperature_c > 60.0f) {
        return ESP_ERR_INVALID_ARG;
    }
    if (client == NULL || !client_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    char topic[OMK_MQTT_TOPIC_SIZE];
    char payload[OMK_MQTT_RELAY_ENVIRONMENT_PAYLOAD_SIZE];
    int written = snprintf(topic, sizeof(topic), "omk-relay/%s/ble/environment", relay_node_id);
    if (written < 0 || written >= (int)sizeof(topic)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(payload, sizeof(payload),
                       "{\"device_key\":\"%s\",\"quality\":\"normal\","
                       "\"temperature_c\":%.1f,\"relative_humidity_percent\":%u,"
                       "\"source\":\"relay\",\"relay_node_id\":\"%s\"}",
                       device_key, (double)temperature_c,
                       relative_humidity_percent, relay_node_id);
    if (written < 0 || written >= (int)sizeof(payload)) {
        return ESP_ERR_INVALID_SIZE;
    }

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
