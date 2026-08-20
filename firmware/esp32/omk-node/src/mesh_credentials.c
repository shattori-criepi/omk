#include "mesh_credentials.h"

#include <string.h>

#include "psa/crypto.h"

static const uint8_t mesh_id_domain[] = "OMK-MESH-ID-v1\0";
static const uint8_t mesh_password_domain[] = "OMK-MESH-PSK-v1\0";
static const uint8_t separator[] = {0};
static const char hex[] = "0123456789abcdef";

static esp_err_t hash_credential_material(const uint8_t *domain, size_t domain_length,
                                          const uint8_t *ssid, size_t ssid_length,
                                          const uint8_t *psk, size_t psk_length,
                                          uint8_t output[32]) {
    uint8_t material[128];
    size_t length = domain_length + ssid_length + sizeof(separator) + psk_length;
    if (length > sizeof(material)) return ESP_ERR_INVALID_SIZE;
    size_t offset = 0;
    memcpy(material + offset, domain, domain_length); offset += domain_length;
    memcpy(material + offset, ssid, ssid_length); offset += ssid_length;
    memcpy(material + offset, separator, sizeof(separator)); offset += sizeof(separator);
    memcpy(material + offset, psk, psk_length);
    size_t output_length = 0;
    psa_status_t result = psa_hash_compute(PSA_ALG_SHA_256, material, length,
                                           output, 32, &output_length);
    memset(material, 0, sizeof(material));
    return result == PSA_SUCCESS && output_length == 32 ? ESP_OK : ESP_FAIL;
}

esp_err_t mesh_credentials_derive(const uint8_t *ssid, size_t ssid_length,
                                  const uint8_t *psk, size_t psk_length,
                                  uint8_t mesh_id[6],
                                  char mesh_ap_password[OMK_MESH_AP_PASSWORD_LENGTH + 1]) {
    if (ssid == NULL || psk == NULL || mesh_id == NULL || mesh_ap_password == NULL ||
        ssid_length == 0 || psk_length < 8) {
        return ESP_ERR_INVALID_ARG;
    }
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;
    uint8_t digest[32];
    esp_err_t err = hash_credential_material(mesh_id_domain, sizeof(mesh_id_domain) - 1,
                                             ssid, ssid_length, psk, psk_length, digest);
    if (err != ESP_OK) return err;
    memcpy(mesh_id, digest, 6);
    err = hash_credential_material(mesh_password_domain, sizeof(mesh_password_domain) - 1,
                                   ssid, ssid_length, psk, psk_length, digest);
    if (err != ESP_OK) return err;
    for (size_t index = 0; index < 16; ++index) {
        mesh_ap_password[index * 2] = hex[digest[index] >> 4];
        mesh_ap_password[index * 2 + 1] = hex[digest[index] & 0x0f];
    }
    mesh_ap_password[OMK_MESH_AP_PASSWORD_LENGTH] = '\0';
    return ESP_OK;
}

bool mesh_credentials_test_vector_matches(void) {
    static const uint8_t ssid[] = "OMK-Test-SSID";
    static const uint8_t psk[] = "correct-horse-battery-staple";
    static const uint8_t expected_id[6] = {0x43, 0x3d, 0x71, 0xb8, 0x6f, 0x8d};
    static const char expected_password[] = "fed195b85f7c0447709a67a3c06e2585";
    uint8_t mesh_id[6];
    char password[OMK_MESH_AP_PASSWORD_LENGTH + 1];
    return mesh_credentials_derive(ssid, sizeof(ssid) - 1, psk, sizeof(psk) - 1,
                                   mesh_id, password) == ESP_OK &&
           memcmp(mesh_id, expected_id, sizeof(mesh_id)) == 0 &&
           strcmp(password, expected_password) == 0;
}
