#pragma once

#include <stdint.h>

#include "esp_err.h"

/* Registers an independent IP-event listener. The MQTT client is started only
 * after the station has an IP address. */
esp_err_t mqtt_registration_start(uint64_t node_id);
