#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "mesh_liveness.h"

typedef struct sen66_measurement sen66_measurement_t;

/* Registers an independent IP-event listener. The MQTT client is started only
 * after the station has an IP address. */
esp_err_t mqtt_registration_start(void);

/* Queues one non-retained mesh diagnostic status payload. */
esp_err_t mqtt_registration_publish_mesh_status(const char *payload);
esp_err_t mqtt_registration_publish_mesh_recovery_status(const char *payload);

/* Counts MQTT_EVENT_DISCONNECTED events for mesh diagnostics. */
uint32_t mqtt_registration_get_disconnect_count(void);
bool mqtt_registration_is_connected(void);
bool mqtt_registration_is_started(void);
/* RAM snapshot only; never takes MQTT's mutex. */
omk_mqtt_progress_t mqtt_registration_get_progress(void);
uint32_t mqtt_registration_get_last_connected_uptime_s(void);
uint32_t mqtt_registration_get_disconnected_duration_s(void);

/* Sensor task: RAM-only updates. Retained status is sent by sen66_tx. */
void mqtt_registration_set_sen66_connected(bool connected);
void mqtt_registration_set_sen66_diagnostics(uint32_t recovery_count,
                                             uint32_t measurement_timeout_count);

/* Queues one non-retained environment measurement for delivery once the
 * established MQTT client is connected. The caller owns neither the MQTT
 * client nor its network task. */
esp_err_t mqtt_registration_publish_environment(const char *sensor_id,
                                                float temperature_c,
                                                uint8_t relative_humidity_percent);

/* Queues a raw SwitchBot BLE observation for Gateway decoding and registry resolution. */
esp_err_t mqtt_registration_publish_ble_relay(const char *relay_node_id,
                                              const char *ble_address, int rssi,
                                              const uint8_t *manufacturer_data, size_t manufacturer_length,
                                              const uint8_t *service_data, size_t service_length);

/* Sender task only: may block on the MQTT library lock. */
esp_err_t mqtt_registration_publish_sen66(const char *sensor_id,
                                          const sen66_measurement_t *measurement);

/* Sender task only: apply the latest registration request and sensor status.
 * May block on MQTT. No application lock is held across MQTT/NVS calls. */
void mqtt_registration_flush_sensor_status(void);
