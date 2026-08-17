#include "node_state.h"

#include "nvs.h"

esp_err_t node_state_verify_provisioning_pop(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t pop[NODE_PROVISIONING_POP_LENGTH];
    size_t pop_length = sizeof(pop);
    err = nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, pop, &pop_length);
    nvs_close(nvs);
    if (err != ESP_OK) {
        return err;
    }
    return pop_length == sizeof(pop) ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
