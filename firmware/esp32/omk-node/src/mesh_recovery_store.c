#include "mesh_recovery_store.h"

#include <limits.h>
#include <string.h>
#include "nvs.h"

#define RECOVERY_NAMESPACE "omk_recovery"
#define RECOVERY_KEY "budget_v1"

esp_err_t mesh_recovery_store_load(omk_mesh_recovery_record_t *record) {
    *record = (omk_mesh_recovery_record_t){.version = 1, .spent = 1};
    nvs_handle_t handle;
    esp_err_t err = nvs_open(RECOVERY_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    omk_mesh_recovery_record_t loaded;
    size_t size = sizeof(loaded);
    err = nvs_get_blob(handle, RECOVERY_KEY, &loaded, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        record->spent = 0;
        return ESP_OK;
    }
    if (err != ESP_OK) return err;
    if (size != sizeof(loaded) || loaded.version != 1 || loaded.spent > 1 ||
        loaded.reason > OMK_RECOVERY_ROOT_LINK) return ESP_ERR_INVALID_STATE;
    *record = loaded;
    return ESP_OK;
}

esp_err_t mesh_recovery_store_save(omk_mesh_recovery_record_t *record, bool spent,
                                  omk_mesh_recovery_reason_t reason) {
    omk_mesh_recovery_record_t next = *record;
    next.version = 1;
    next.spent = spent;
    if (spent) {
        next.reason = reason;
        if (next.restart_count < UINT32_MAX) ++next.restart_count;
    }
    nvs_handle_t handle;
    esp_err_t err = nvs_open(RECOVERY_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(handle, RECOVERY_KEY, &next, sizeof(next));
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    if (err != ESP_OK) return err;
    /* Do not use load(): a missing record during verification is a failure. */
    err = nvs_open(RECOVERY_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) return err;
    omk_mesh_recovery_record_t verified;
    size_t size = sizeof(verified);
    err = nvs_get_blob(handle, RECOVERY_KEY, &verified, &size);
    nvs_close(handle);
    if (err != ESP_OK) return err;
    if (size != sizeof(verified) || memcmp(&next, &verified, sizeof(next)) != 0)
        return ESP_ERR_INVALID_STATE;
    *record = next;
    return ESP_OK;
}
