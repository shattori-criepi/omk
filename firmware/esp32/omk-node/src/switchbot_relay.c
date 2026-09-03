#include "switchbot_relay.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "mqtt_registration.h"
#include "node_identity.h"

static const char *TAG = "switchbot_relay";
#define SWITCHBOT_RELAY_INTERVAL_US (10LL * 1000LL * 1000LL)
#define SWITCHBOT_FRAGMENT_JOIN_WINDOW_US (2LL * 1000LL * 1000LL)
#define SWITCHBOT_RELAY_DEVICE_SLOTS 16
typedef struct {
    bool in_use;
    uint8_t address[6];
    uint8_t manufacturer_data[31];
    uint8_t service_data[31];
    size_t manufacturer_length;
    size_t service_length;
    int64_t last_fragment_us;
    uint32_t payload_hash;
    int64_t last_publish_us;
} relay_device_slot_t;
static relay_device_slot_t relay_device_slots[SWITCHBOT_RELAY_DEVICE_SLOTS];
static bool node_identity_error_logged;
static uint32_t hash_bytes(uint32_t value, const uint8_t *data, size_t length) { for (size_t i = 0; i < length; ++i) value = (value ^ data[i]) * 16777619u; return value; }
static bool should_publish(const uint8_t address[6], uint32_t payload_hash, int64_t now_us) {
    relay_device_slot_t *available = NULL;
    for (size_t i = 0; i < SWITCHBOT_RELAY_DEVICE_SLOTS; ++i) {
        relay_device_slot_t *slot = &relay_device_slots[i];
        if (slot->in_use && memcmp(slot->address, address, 6) == 0) {
            if (slot->payload_hash == payload_hash && now_us - slot->last_publish_us < SWITCHBOT_RELAY_INTERVAL_US) return false;
            slot->payload_hash = payload_hash; slot->last_publish_us = now_us; return true;
        }
        if (!slot->in_use && available == NULL) available = slot;
    }
    if (available == NULL) { ESP_LOGW(TAG, "Relay device slots exhausted; ignoring BLE observation"); return false; }
    available->in_use = true; memcpy(available->address, address, 6); available->payload_hash = payload_hash; available->last_publish_us = now_us; return true;
}
static relay_device_slot_t *find_slot(const uint8_t address[6]) {
    relay_device_slot_t *available = NULL;
    for (size_t i = 0; i < SWITCHBOT_RELAY_DEVICE_SLOTS; ++i) {
        relay_device_slot_t *slot = &relay_device_slots[i];
        if (slot->in_use && memcmp(slot->address, address, 6) == 0) return slot;
        if (!slot->in_use && available == NULL) available = slot;
    }
    if (available != NULL) { available->in_use = true; memcpy(available->address, address, 6); }
    return available;
}
void switchbot_relay_handle_observation(const uint8_t address[6], int rssi, const uint8_t *manufacturer_data, size_t manufacturer_length, const uint8_t *service_data, size_t service_length) {
    if (address == NULL || manufacturer_length > 31 || service_length > 31 || (manufacturer_length == 0 && service_length == 0)) return;
    int64_t now_us = esp_timer_get_time();
    relay_device_slot_t *slot = find_slot(address);
    if (slot == NULL) { ESP_LOGW(TAG, "Relay device slots exhausted; ignoring BLE observation"); return; }
    /* Active scan can report ADV and SCAN_RSP separately. Keep fragments long
     * enough to combine one advertisement, but never carry stale data into a
     * later packet from the same device. */
    if (now_us - slot->last_fragment_us > SWITCHBOT_FRAGMENT_JOIN_WINDOW_US) {
        slot->manufacturer_length = 0;
        slot->service_length = 0;
    }
    if (manufacturer_length > 0) { memcpy(slot->manufacturer_data, manufacturer_data, manufacturer_length); slot->manufacturer_length = manufacturer_length; }
    if (service_length > 0) { memcpy(slot->service_data, service_data, service_length); slot->service_length = service_length; }
    slot->last_fragment_us = now_us;
    uint32_t payload_hash = hash_bytes(hash_bytes(2166136261u, slot->manufacturer_data, slot->manufacturer_length), slot->service_data, slot->service_length);
    if (!should_publish(address, payload_hash, esp_timer_get_time())) return;
    char relay_node_id[OMK_NODE_ID_HEX_LENGTH + 1];
    esp_err_t err = node_identity_get_id_hex(relay_node_id, sizeof(relay_node_id));
    if (err != ESP_OK) { if (!node_identity_error_logged) ESP_LOGW(TAG, "Could not get relay Node ID: %s", esp_err_to_name(err)); node_identity_error_logged = true; return; }
    node_identity_error_logged = false;
    char ble_address[13];
    if (snprintf(ble_address, sizeof(ble_address), "%02x%02x%02x%02x%02x%02x", address[0], address[1], address[2], address[3], address[4], address[5]) != 12) return;
    err = mqtt_registration_publish_ble_relay(relay_node_id, ble_address, rssi,
                                              slot->manufacturer_data, slot->manufacturer_length,
                                              slot->service_data, slot->service_length);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) ESP_LOGW(TAG, "Could not queue BLE raw relay: %s", esp_err_to_name(err));
}
