#pragma once

#include <stdint.h>

#include "esp_err.h"

/* Starts the always-available USB Serial/JTAG provisioning transport.
 * The Wi-Fi driver must already have been prepared by wifi_station_prepare(). */
esp_err_t usb_provisioning_start(uint64_t node_id);
