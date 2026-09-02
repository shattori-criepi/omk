#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct sen66_measurement sen66_measurement_t;

/* Registers an independent IP-event listener. The MQTT client is started only
 * after the station has an IP address. */
esp_err_t mqtt_registration_start(void);

/* Queues one non-retained mesh diagnostic status payload. */
esp_err_t mqtt_registration_publish_mesh_status(const char *payload);

/* Counts MQTT_EVENT_DISCONNECTED events for mesh diagnostics. */
uint32_t mqtt_registration_get_disconnect_count(void);

/* Updates the retained Node registration status with physically detected SEN66 state. */
void mqtt_registration_set_sen66_connected(bool connected);
void mqtt_registration_set_sen66_diagnostics(uint32_t recovery_count,
                                             uint32_t measurement_timeout_count);

/* Queues one non-retained environment measurement for delivery once the
 * established MQTT client is connected. The caller owns neither the MQTT
 * client nor its network task. */
esp_err_t mqtt_registration_publish_environment(const char *sensor_id,
                                                float temperature_c,
                                                uint8_t relative_humidity_percent);

/* Queues a relay-origin BLE observation for Gateway registry resolution. */
esp_err_t mqtt_registration_publish_relay_environment(const char *device_key,
                                                       float temperature_c,
                                                       uint8_t relative_humidity_percent,
                                                       const char *relay_node_id);

/* Queues one non-retained SEN66 measurement through the established MQTT client. */
esp_err_t mqtt_registration_publish_sen66(const char *sensor_id,
                                          const sen66_measurement_t *measurement);
