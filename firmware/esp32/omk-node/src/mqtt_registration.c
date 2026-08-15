#include "mqtt_registration.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "mqtt_client.h"
#include "node_registration.h"
#include "node_protocol.h"

#define OMK_MQTT_BROKER_URI "mqtt://192.168.50.1:1883"
#define OMK_MQTT_TOPIC_SIZE 80
#define OMK_MQTT_PAYLOAD_SIZE 128
#define OMK_MQTT_CLIENT_ID_SIZE 32
static const char *TAG = "omk-mqtt";
static esp_mqtt_client_handle_t client;
static bool client_started;
static bool ip_handler_registered;
static char registration_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_config_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_ack_topic[OMK_MQTT_TOPIC_SIZE];
static char registration_payload[OMK_MQTT_PAYLOAD_SIZE];
static char client_id[OMK_MQTT_CLIENT_ID_SIZE];

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

esp_err_t mqtt_registration_start(uint64_t node_id) {
    if (ip_handler_registered) {
        return ESP_ERR_INVALID_STATE;
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

    esp_err_t err = esp_mqtt_client_register_event(client, MQTT_EVENT_ANY,
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
