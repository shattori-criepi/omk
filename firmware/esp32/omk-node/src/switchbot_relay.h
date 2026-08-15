#pragma once

#include <stddef.h>
#include <stdint.h>

/* Handles the SwitchBot manufacturer bytes after the two-byte Company ID.
 * This is deliberately limited to the one Meter used for the first OMK
 * BLE-to-Wi-Fi relay validation. */
void switchbot_relay_handle_manufacturer_data(const uint8_t *data, size_t length);
