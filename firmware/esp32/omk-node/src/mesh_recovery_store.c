#include "mesh_recovery_store.h"

#include "nvs.h"

#define RECOVERY_NAMESPACE "omk_recovery"
#define RECOVERY_KEY "budget_v1"

esp_err_t mesh_recovery_store_load(omk_mesh_recovery_record_t *record) {
    *record = (omk_mesh_recovery_record_t){.version = 1};
    nvs_handle_t handle;
    esp_err_t err = nvs_open(RECOVERY_NAMESPACE, NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    omk_mesh_recovery_record_t loaded;
    size_t size = sizeof(loaded);
    err = nvs_get_blob(handle, RECOVERY_KEY, &loaded, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    if (size != sizeof(loaded) || loaded.version != 1 || loaded.spent > 1 ||
        loaded.reason > OMK_RECOVERY_IP_TIMEOUT) return ESP_ERR_INVALID_STATE;
    *record = loaded;
    return ESP_OK;
}
