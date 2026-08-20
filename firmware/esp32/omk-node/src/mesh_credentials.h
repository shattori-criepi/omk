#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define OMK_MESH_AP_PASSWORD_LENGTH 32

/* Input bytes are exactly domain || ssid || 0x00 || psk. */
esp_err_t mesh_credentials_derive(const uint8_t *ssid, size_t ssid_length,
                                  const uint8_t *psk, size_t psk_length,
                                  uint8_t mesh_id[6],
                                  char mesh_ap_password[OMK_MESH_AP_PASSWORD_LENGTH + 1]);

/* Deterministic non-secret vector to guard the byte-level derivation format. */
bool mesh_credentials_test_vector_matches(void);
