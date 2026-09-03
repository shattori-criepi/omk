#pragma once

#include <stddef.h>
#include <stdint.h>

/* Relay a raw SwitchBot observation. Device classification and decoding stay
 * on the Gateway so every supported SwitchBot model shares one decoder. */
void switchbot_relay_handle_observation(const uint8_t address[6], int rssi,
                                        const uint8_t *manufacturer_data, size_t manufacturer_length,
                                        const uint8_t *service_data, size_t service_length);
