#pragma once

#include <stdint.h>

#include "esp_err.h"

/* Registers an independent IP-event listener. The MQTT client is started only
 * after the station has an IP address. */
esp_err_t mqtt_registration_start(uint64_t node_id);

/* Queues one non-retained environment measurement for delivery once the
 * established MQTT client is connected. The caller owns neither the MQTT
 * client nor its network task. */
esp_err_t mqtt_registration_publish_environment(const char *sensor_id,
                                                float temperature_c,
                                                uint8_t relative_humidity_percent);
