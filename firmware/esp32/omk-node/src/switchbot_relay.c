#include "switchbot_relay.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

#include "mqtt_registration.h"
#include "node_identity.h"

static const char *TAG = "switchbot_relay";

#define SWITCHBOT_METER_PHYSICAL_ID_LENGTH 6
#define SWITCHBOT_METER_MANUFACTURER_LENGTH 11
#define SWITCHBOT_METER_LAYOUT_MARKER_INDEX 7
#define SWITCHBOT_METER_LAYOUT_MARKER 0x03
#define SWITCHBOT_METER_MEASUREMENT_OFFSET 8
#define SWITCHBOT_ENVIRONMENT_MIN_TEMPERATURE_C (-20.0f)
#define SWITCHBOT_ENVIRONMENT_MAX_TEMPERATURE_C 60.0f
#define SWITCHBOT_ENVIRONMENT_PUBLISH_INTERVAL_US (10LL * 1000LL * 1000LL)
#define SWITCHBOT_RELAY_DEVICE_SLOTS 8

typedef struct {
    bool in_use;
    uint8_t physical_id[SWITCHBOT_METER_PHYSICAL_ID_LENGTH];
    int64_t last_publish_attempt_us;
} relay_device_slot_t;

static relay_device_slot_t relay_device_slots[SWITCHBOT_RELAY_DEVICE_SLOTS];
static bool node_identity_error_logged;

static bool decode_meter(const uint8_t *data, size_t length,
                         uint8_t physical_id[SWITCHBOT_METER_PHYSICAL_ID_LENGTH],
                                float *temperature_c, uint8_t *humidity_percent) {
    if (data == NULL || length != SWITCHBOT_METER_MANUFACTURER_LENGTH ||
        data[SWITCHBOT_METER_LAYOUT_MARKER_INDEX] != SWITCHBOT_METER_LAYOUT_MARKER) {
        return false;
    }

    uint8_t fraction = data[SWITCHBOT_METER_MEASUREMENT_OFFSET];
    uint8_t signed_integer = data[SWITCHBOT_METER_MEASUREMENT_OFFSET + 1];
    uint8_t humidity = data[SWITCHBOT_METER_MEASUREMENT_OFFSET + 2];
    if (fraction > 9 || humidity > 100) {
        return false;
    }

    float temperature = (float)(signed_integer & 0x7f) + (float)fraction / 10.0f;
    if ((signed_integer & 0x80) == 0) {
        temperature = -temperature;
    }
    if (temperature < SWITCHBOT_ENVIRONMENT_MIN_TEMPERATURE_C ||
        temperature > SWITCHBOT_ENVIRONMENT_MAX_TEMPERATURE_C) {
        return false;
    }

    memcpy(physical_id, data, SWITCHBOT_METER_PHYSICAL_ID_LENGTH);
    *temperature_c = temperature;
    *humidity_percent = humidity;
    return true;
}

static bool publish_interval_elapsed(const uint8_t physical_id[SWITCHBOT_METER_PHYSICAL_ID_LENGTH],
                                     int64_t now_us) {
    relay_device_slot_t *available = NULL;
    for (size_t index = 0; index < SWITCHBOT_RELAY_DEVICE_SLOTS; ++index) {
        relay_device_slot_t *slot = &relay_device_slots[index];
        if (slot->in_use && memcmp(slot->physical_id, physical_id,
                                   SWITCHBOT_METER_PHYSICAL_ID_LENGTH) == 0) {
            if (now_us - slot->last_publish_attempt_us < SWITCHBOT_ENVIRONMENT_PUBLISH_INTERVAL_US) {
                return false;
            }
            slot->last_publish_attempt_us = now_us;
            return true;
        }
        if (!slot->in_use && available == NULL) {
            available = slot;
        }
    }
    if (available == NULL) {
        ESP_LOGW(TAG, "Relay device slots exhausted; ignoring Meter advertisement");
        return false;
    }
    memcpy(available->physical_id, physical_id, SWITCHBOT_METER_PHYSICAL_ID_LENGTH);
    available->last_publish_attempt_us = now_us;
    available->in_use = true;
    return true;
}

void switchbot_relay_handle_manufacturer_data(const uint8_t *data, size_t length) {
    uint8_t physical_id[SWITCHBOT_METER_PHYSICAL_ID_LENGTH];
    float temperature_c;
    uint8_t humidity_percent;
    if (!decode_meter(data, length, physical_id, &temperature_c, &humidity_percent)) {
        return;
    }

    int64_t now_us = esp_timer_get_time();
    if (!publish_interval_elapsed(physical_id, now_us)) {
        return;
    }

    char device_key[sizeof("switchbot:") + SWITCHBOT_METER_PHYSICAL_ID_LENGTH * 2];
    int written = snprintf(device_key, sizeof(device_key),
                           "switchbot:%02x%02x%02x%02x%02x%02x",
                           physical_id[0], physical_id[1], physical_id[2],
                           physical_id[3], physical_id[4], physical_id[5]);
    if (written < 0 || written >= (int)sizeof(device_key)) {
        ESP_LOGW(TAG, "Could not build Meter device key");
        return;
    }

    char relay_node_id[OMK_NODE_ID_HEX_LENGTH + 1];
    esp_err_t err = node_identity_get_id_hex(relay_node_id, sizeof(relay_node_id));
    if (err != ESP_OK) {
        if (!node_identity_error_logged) {
            ESP_LOGW(TAG, "Could not get relay Node ID: %s", esp_err_to_name(err));
            node_identity_error_logged = true;
        }
        return;
    }
    node_identity_error_logged = false;

    err = mqtt_registration_publish_relay_environment(
        device_key, temperature_c, humidity_percent, relay_node_id);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Published Meter relay device_key=%s temperature=%.1f humidity=%u",
                 device_key, (double)temperature_c, humidity_percent);
    } else if (err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Could not queue Meter environment: %s", esp_err_to_name(err));
    }
}
