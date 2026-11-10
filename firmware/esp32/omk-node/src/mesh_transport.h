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
    uint32_t tx_accepted;
    uint32_t tx_dropped;
    uint32_t rx_dropped;
    uint32_t mqtt_published; /* Accepted into ESP-MQTT, not a Gateway ACK. */
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
