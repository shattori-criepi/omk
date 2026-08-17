#include "node_identity.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>

#include "esp_mac.h"

static bool node_id_initialized;
static uint64_t cached_node_id;

esp_err_t node_identity_get_id(uint64_t *node_id) {
    if (node_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!node_id_initialized) {
        uint8_t mac[6];
        esp_err_t err = esp_efuse_mac_get_default(mac);
        if (err != ESP_OK) {
            return err;
        }
        uint64_t hash = 14695981039346656037ULL;
        for (size_t i = 0; i < sizeof(mac); ++i) {
            hash ^= mac[i];
            hash *= 1099511628211ULL;
        }
        cached_node_id = hash & 0x0000ffffffffffffULL;
        node_id_initialized = true;
    }
    *node_id = cached_node_id;
    return ESP_OK;
}

esp_err_t node_identity_get_id_hex(char *node_id, size_t size) {
    if (node_id == NULL || size < OMK_NODE_ID_HEX_LENGTH + 1) {
        return ESP_ERR_INVALID_ARG;
    }

    uint64_t id;
    esp_err_t err = node_identity_get_id(&id);
    if (err != ESP_OK) {
        return err;
    }
    int written = snprintf(node_id, size, "%012" PRIx64, id);
    return written == OMK_NODE_ID_HEX_LENGTH ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
