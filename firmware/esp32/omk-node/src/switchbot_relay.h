#pragma once

#include <stddef.h>
#include <stdint.h>

/* Relay raw bytes. Minimal motion/contact state hints prioritize observed
 * transitions; authoritative classification/decoding stays on the Gateway.
 * The BLE callback owns this state. Two pending transitions/device expire
 * after 30s; retry requires a fresh advertisement. RF events never observed,
 * overflow, and events lost after transport acceptance cannot be recovered. */
void switchbot_relay_handle_observation(const uint8_t address[6], int rssi,
                                        const uint8_t *manufacturer_data, size_t manufacturer_length,
                                        const uint8_t *service_data, size_t service_length);
