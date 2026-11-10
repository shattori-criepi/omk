#include "node_registration.h"

#include <string.h>
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "node_protocol.h"
#include "node_state.h"

#define REGISTRATION_KEY "reg_state"
#define REGISTRATION_MAGIC UINT32_C(0x4f4d4b52)
#define REGISTRATION_VERSION 1U
/* One NVS blob is the atomic unit. Cleared records retain their revision so
 * an old retained command cannot resurrect a removed identity. */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t revision;
    uint8_t registered;
    char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    uint8_t reserved[2];
} registration_record_t;
_Static_assert(sizeof(registration_record_t) == 64, "Stable NVS registration format");
static portMUX_TYPE id_lock = portMUX_INITIALIZER_UNLOCKED;
static registration_record_t cached_record;
static esp_err_t cached_error = ESP_ERR_INVALID_STATE;
static bool initialized;
static bool writer_active;

static void make_record(registration_record_t *record, uint32_t revision, const char *id) {
    memset(record, 0, sizeof(*record));
    record->magic = REGISTRATION_MAGIC;
    record->version = REGISTRATION_VERSION;
    record->revision = revision;
    record->registered = id != NULL;
    if (id != NULL) strcpy(record->logical_id, id);
}
static bool valid_record(const registration_record_t *record) {
    return record->magic == REGISTRATION_MAGIC && record->version == REGISTRATION_VERSION &&
           record->registered <= 1 && record->reserved[0] == 0 && record->reserved[1] == 0 &&
           memchr(record->logical_id, 0, sizeof(record->logical_id)) != NULL &&
           (record->registered ? record->logical_id[0] != 0 : record->logical_id[0] == 0);
}
static esp_err_t load_record(registration_record_t *record) {
    make_record(record, 0, NULL);
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    size_t size = sizeof(*record);
    err = nvs_get_blob(nvs, REGISTRATION_KEY, record, &size);
    if (err == ESP_OK) {
        if (size != sizeof(*record) || !valid_record(record)) err = ESP_FAIL;
    } else if (err == ESP_ERR_NVS_NOT_FOUND) {
        /* Legacy keys are read only when the blob is absent. Migration is
         * logical revision zero; the first newer command persists the blob. */
        make_record(record, 0, NULL);
        uint8_t registered = 0;
        err = nvs_get_u8(nvs, "registered", &registered);
        if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
        if (err == ESP_OK && registered > 1) err = ESP_FAIL;
        if (err == ESP_OK && registered == 1) {
            size = sizeof(record->logical_id);
            err = nvs_get_str(nvs, "logical_id", record->logical_id, &size);
            record->registered = 1;
            if (err == ESP_OK && !valid_record(record)) err = ESP_FAIL;
        }
    }
    nvs_close(nvs);
    return err;
}
static void publish_record(const registration_record_t *record, esp_err_t err) {
    portENTER_CRITICAL(&id_lock);
    if (err == ESP_OK) cached_record = *record;
    cached_error = err;
    initialized = true;
    portEXIT_CRITICAL(&id_lock);
}
/* Readers never touch NVS. Concurrent writers get a bounded retry error. */
static bool begin_write(void) {
    portENTER_CRITICAL(&id_lock);
    bool allowed = !writer_active;
    if (allowed) writer_active = true;
    portEXIT_CRITICAL(&id_lock);
    return allowed;
}
static void end_write(void) {
    portENTER_CRITICAL(&id_lock);
    writer_active = false;
    portEXIT_CRITICAL(&id_lock);
}
static esp_err_t ensure_initialized(void) {
    portENTER_CRITICAL(&id_lock);
    bool ready = initialized;
    esp_err_t err = cached_error;
    portEXIT_CRITICAL(&id_lock);
    if (!ready || err != ESP_OK) {
        registration_record_t record;
        err = load_record(&record);
        publish_record(&record, err);
    }
    return err;
}
void node_registration_init_cache(void) {
    if (!begin_write()) return;
    registration_record_t record;
    esp_err_t err = load_record(&record);
    publish_record(&record, err);
    end_write();
}
esp_err_t node_registration_snapshot(char *id, size_t size, uint32_t *revision) {
    if (id == NULL || size == 0 || revision == NULL) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&id_lock);
    esp_err_t err = cached_error;
    if (err == ESP_OK) {
        if (size <= strlen(cached_record.logical_id)) err = ESP_ERR_NVS_INVALID_LENGTH;
        else {
            strcpy(id, cached_record.logical_id);
            *revision = cached_record.revision;
        }
    }
    portEXIT_CRITICAL(&id_lock);
    return err;
}
uint32_t node_registration_get_revision(void) {
    portENTER_CRITICAL(&id_lock);
    uint32_t revision = cached_error == ESP_OK ? cached_record.revision : 0;
    portEXIT_CRITICAL(&id_lock);
    return revision;
}
esp_err_t node_registration_get_logical_id(char *id, size_t size) {
    uint32_t revision;
    esp_err_t err = node_registration_snapshot(id, size, &revision);
    return err == ESP_OK && id[0] == 0 ? ESP_ERR_NVS_NOT_FOUND : err;
}
esp_err_t node_registration_get_provisioning_state(bool has_wifi_credentials, uint8_t *state) {
    if (state == NULL) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&id_lock);
    esp_err_t err = cached_error;
    bool registered = cached_record.registered != 0;
    portEXIT_CRITICAL(&id_lock);
    if (err != ESP_OK) return err;
    *state = registered ? OMK_NODE_PROVISIONING_STATE_REGISTERED :
        (has_wifi_credentials ? OMK_NODE_PROVISIONING_STATE_PROVISIONED :
         OMK_NODE_PROVISIONING_STATE_UNREGISTERED);
    return ESP_OK;
}
static esp_err_t apply_locked(uint32_t revision, const char *id) {
    esp_err_t err = ensure_initialized();
    if (err != ESP_OK) return err;
    registration_record_t next;
    make_record(&next, revision, id);
    portENTER_CRITICAL(&id_lock);
    registration_record_t previous = cached_record;
    portEXIT_CRITICAL(&id_lock);
    if (revision < previous.revision) return ESP_ERR_INVALID_STATE;
    if (revision == previous.revision) {
        return next.registered == previous.registered &&
               strcmp(next.logical_id, previous.logical_id) == 0 ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    nvs_handle_t nvs;
    err = nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(nvs, REGISTRATION_KEY, &next, sizeof(next));
    if (err == ESP_OK) err = nvs_commit(nvs);
    if (err == ESP_OK) {
        registration_record_t readback;
        size_t size = sizeof(readback);
        err = nvs_get_blob(nvs, REGISTRATION_KEY, &readback, &size);
        if (err == ESP_OK && (size != sizeof(readback) || memcmp(&next, &readback, size) != 0)) err = ESP_FAIL;
    }
    nvs_close(nvs);
    /* A write failure may have an uncertain persistent outcome. Fail closed
     * until reload; reboot can see only the complete old or new NVS blob. */
    publish_record(&next, err);
    return err;
}
esp_err_t node_registration_apply(uint32_t revision, const char *logical_id) {
    if (logical_id != NULL && (logical_id[0] == 0 ||
        strlen(logical_id) > OMK_NODE_LOGICAL_ID_MAX_LENGTH)) return ESP_ERR_INVALID_ARG;
    if (!begin_write()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = apply_locked(revision, logical_id);
    end_write();
    return err;
}
static esp_err_t local_change(const char *id) {
    if (!begin_write()) return ESP_ERR_INVALID_STATE;
    esp_err_t err = ensure_initialized();
    if (err == ESP_OK) {
        uint32_t revision = node_registration_get_revision();
        err = revision == UINT32_MAX ? ESP_ERR_INVALID_STATE : apply_locked(revision + 1, id);
    }
    end_write();
    return err;
}
esp_err_t node_registration_save(const char *logical_id) {
    if (logical_id == NULL || logical_id[0] == 0 ||
        strlen(logical_id) > OMK_NODE_LOGICAL_ID_MAX_LENGTH) return ESP_ERR_INVALID_ARG;
    return local_change(logical_id);
}
esp_err_t node_registration_clear(void) { return local_change(NULL); }
esp_err_t node_registration_clear_for_development(void) { return node_registration_clear(); }
