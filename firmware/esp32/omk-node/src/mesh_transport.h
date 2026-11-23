#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "mesh_wire.h"

typedef struct {
    bool started;
    bool parent_connected;
    bool is_root;
    bool has_ip;
    bool mqtt_connected;
    bool rootless;
    int layer;
    uint8_t parent_mac[6];
    uint8_t root_mac[6]; /* Zero when unknown/disconnected/rootless. */
    uint8_t self_ap_mac[6]; /* AP BSSID, not the hashed OMK Node ID. */
    uint32_t parent_disconnect_count; /* Raw Mesh events, per boot. */
    uint32_t root_change_count; /* Distinct nonzero ROOT_ADDRESS changes, excluding first. */
    bool last_disconnect_valid;
    uint8_t last_disconnect_reason;
    int8_t last_disconnect_rssi; /* Historical event RSSI, never current RSSI. */
    uint32_t tx_accepted;
    uint32_t tx_dropped;
    uint32_t rx_dropped;
    uint32_t mqtt_published; /* Accepted into ESP-MQTT, not a Gateway ACK. */
    uint32_t mesh_tx_ok; /* SDK send returns ESP_OK, not Gateway delivery. */
    uint32_t mesh_tx_errors;
    uint32_t mesh_rx_ok; /* SDK receives, including subsequently rejected frames. */
    uint32_t mesh_rx_errors; /* Excludes normal receive-wait timeouts. */
    /* Bounded per-boot diagnostics (saturate, never wrap). API acceptance,
     * subsequent topology and broker PUBACK are separate observations. */
    uint8_t recovery_action;
    uint16_t recovery_requests, recovery_ok, recovery_link, mqtt_pubacks;
    uint16_t recovery_votes, recovery_demotions;
    int32_t recovery_error;
} mesh_transport_status_t;

/* After esp_netif_init/default loop, before esp_wifi_init. Called once. */
esp_err_t mesh_transport_init(void);
/* After wifi_station_prepare, before starting sensors. Called once. */
esp_err_t mesh_transport_start(void);
/* Bounded copy, never waits for Mesh/MQTT. Success means local queue acceptance. */
esp_err_t mesh_transport_publish(mesh_message_kind_t kind, const char *payload, size_t length);
void mesh_transport_get_status(mesh_transport_status_t *status);
/* After applying a new registration revision, discard locally queued old data. */
void mesh_transport_drop_pending(void);
