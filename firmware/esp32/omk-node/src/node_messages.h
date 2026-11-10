#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct sen66_measurement sen66_measurement_t;

/* Application records, not MQTT sessions. Publishers never wait for network IO. */
esp_err_t node_messages_start(void);
void node_messages_handle_config(const char *payload, size_t length);
void node_messages_set_sen66_connected(bool connected);
void node_messages_set_sen66_diagnostics(uint32_t recoveries, uint32_t timeouts);
esp_err_t node_messages_publish_sen66(const char *logical_id, const sen66_measurement_t *sample);
esp_err_t node_messages_publish_ble_relay(const char *relay_node_id,
    const char *ble_address, int rssi, const uint8_t *manufacturer_data,
    size_t manufacturer_length, const uint8_t *service_data, size_t service_length);
