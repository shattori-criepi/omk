#include "diag_usb.h"
#include "mesh_diag.h"
#include "mesh_probe.h"
#if OMK_DIAGNOSTICS
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"

#define DIAG_USB_MAX_READ 48U
static char response[256]; /* Existing USB owner; no concurrent writers. */

static bool reply(const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    int length = vsnprintf(response, sizeof(response), format, arguments);
    va_end(arguments);
    if (length <= 0 || length >= (int)sizeof(response)) return false;
    /* A complete JSON line, including newline, enters the driver in one call.
     * Console FIFO traffic can still interfere; the host checks CRC/retries. */
    return usb_serial_jtag_write_bytes(response, (size_t)length, pdMS_TO_TICKS(100)) == length;
}

static void reply_status(const char *node, const char *command, const char *state) {
    (void)reply("{\"status\":\"%s\",\"protocol_version\":2,\"node_id\":\"%s\","
                "\"command\":\"%s\"}\n", state, node, command);
}

static bool number(const cJSON *root, const char *key, uint32_t min, uint32_t max, uint32_t *value) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(item) || !(item->valuedouble >= min && item->valuedouble <= max)) return false;
    uint32_t candidate = (uint32_t)item->valuedouble;
    if ((double)candidate != item->valuedouble) return false;
    *value = candidate;
    return true;
}

bool diag_usb_handle(const cJSON *request, const char *command, const char *node_id) {
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE
    bool v2_command=!strcmp(command,"diag_v2");
#else
    bool v2_command=false;
#endif
    if (strcmp(command, "diag_info") && strcmp(command, "diag_live") &&
        strcmp(command, "diag_read") && strcmp(command, "diag_arm") && strcmp(command, "diag_state") && !v2_command) return false;
    const cJSON *expected = cJSON_GetObjectItemCaseSensitive(request, "expected_node_id");
    uint32_t version;
    if (!number(request, "protocol_version", 2, 2, &version)) {
        reply_status(node_id, command, "invalid_request"); return true;
    }
    if (!cJSON_IsString(expected) || !expected->valuestring ||
        strlen(expected->valuestring) != 12 || strcmp(expected->valuestring, node_id)) {
        reply_status(node_id, command, "node_identity_changed"); return true;
    }
    if (!strcmp(command, "diag_info")) {
        diag_store_status_t store = diag_store_status();
        (void)reply("{\"status\":\"ok\",\"protocol_version\":2,\"node_id\":\"%s\",\"command\":\"diag_info\","
                    "\"ready\":%" PRIu32 ",\"occupied\":%" PRIu32 ",\"valid\":%" PRIu32 ",\"attempt\":%" PRIu32 ","
                    "\"error\":%" PRId32 ",\"flash\":%u,\"live\":%u,\"io_us\":%" PRIu32 ",\"max_io_us\":%" PRIu32 ",\"revision\":%" PRIu32 "}\n",
                    node_id, store.ready, store.occupied_mask, store.valid_mask, store.attempt_mask,
                    store.error, DIAG_STORE_SIZE, (unsigned)sizeof(diag_core_t), store.last_io_us, store.max_io_us, diag_store_revision());
        return true;
    }
    if (!strcmp(command, "diag_state")) {
        char boot[17]; uint64_t start; uint32_t generation, reason;
        mesh_diag_state(boot, &start, &generation, &reason);
        (void)reply("{\"status\":\"ok\",\"protocol_version\":2,\"node_id\":\"%s\",\"command\":\"diag_state\","
                    "\"boot_id\":\"%s\",\"start_ms\":%" PRIu64 ",\"arm\":%" PRIu32 ",\"trigger\":%" PRIu32 ","
                    "\"phase\":%" PRIu32 ",\"block\":%" PRIu32
                    "}\n",
                    node_id, boot, start, generation, reason, diag_store_phase(), diag_store_block_reason());
        return true;
    }
    if (!strcmp(command, "diag_live")) {
        size_t length = 0;
        uint32_t token = mesh_diag_live_begin(&length);
        (void)reply("{\"status\":\"ok\",\"protocol_version\":2,\"node_id\":\"%s\",\"command\":\"diag_live\","
                    "\"token\":%" PRIu32 ",\"length\":%u,\"crc32\":\"%08" PRIx32 "\"}\n",
                    node_id, token, (unsigned)length, mesh_diag_live_crc32());
        return true;
    }
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE
    if(v2_command) {
        size_t length=0; uint32_t token=mesh_diag_v2_begin(&length);
        (void)reply("{\"status\":\"ok\",\"protocol_version\":2,\"node_id\":\"%s\",\"command\":\"diag_v2\","
                    "\"token\":%" PRIu32 ",\"length\":%u,\"crc32\":\"%08" PRIx32 "\"}\n",
                    node_id,token,(unsigned)length,mesh_diag_live_crc32());
        return true;
    }
#endif
    if (!strcmp(command, "diag_arm")) {
        const cJSON *confirm = cJSON_GetObjectItemCaseSensitive(request, "confirm");
        if (!cJSON_IsString(confirm) || !confirm->valuestring ||
            strcmp(confirm->valuestring, "ERASE_DIAGNOSTIC_EVIDENCE")) {
            reply_status(node_id, command, "confirmation_required"); return true;
        }
        uint32_t backed_up, current;
        const cJSON *expected_boot = cJSON_GetObjectItemCaseSensitive(request, "expected_boot_id");
        char boot[17]; uint64_t start; uint32_t generation, reason;
        mesh_diag_state(boot, &start, &generation, &reason);
        if (!cJSON_IsString(expected_boot) || !expected_boot->valuestring ||
            strcmp(boot, expected_boot->valuestring)) {
            reply_status(node_id, command, "boot_identity_changed"); return true;
        }
        if (!number(request, "backup_crc32", 0, UINT32_MAX, &backed_up)) {
            reply_status(node_id, command, "backup_required"); return true;
        }
        if (diag_store_flash_crc32(&current) != ESP_OK) {
            reply_status(node_id, command, "storage_error"); return true;
        }
        /* Same USB task owns save/erase, so no persistence can race this check. */
        if (backed_up != current) {
            reply_status(node_id, command, "backup_changed"); return true;
        }
        reply_status(node_id, command, mesh_diag_rearm() == ESP_OK ? "accepted" : "storage_error");
        return true;
    }
    const cJSON *space = cJSON_GetObjectItemCaseSensitive(request, "space");
    bool is_live = cJSON_IsString(space) && space->valuestring && !strcmp(space->valuestring, "live");
    bool is_flash = cJSON_IsString(space) && space->valuestring && !strcmp(space->valuestring, "flash");
    bool is_v2=false;
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE
    is_v2=cJSON_IsString(space) && space->valuestring && !strcmp(space->valuestring,"v2");
#endif
    uint32_t offset, count, token = 0;
    uint32_t limit = is_live ? (uint32_t)sizeof(diag_core_t) : DIAG_STORE_SIZE;
    if(is_v2) limit=sizeof(mesh_probe_core_t);
    if ((!is_live && !is_flash && !is_v2) || !number(request, "offset", 0, limit, &offset) ||
        !number(request, "count", 1, DIAG_USB_MAX_READ, &count) || count > limit - offset ||
        ((is_live || is_v2) && !number(request, "token", 1, UINT32_MAX, &token))) {
        reply_status(node_id, command, "invalid_request"); return true;
    }
    uint8_t data[DIAG_USB_MAX_READ];
    esp_err_t err;
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE
    if(is_v2) err=mesh_diag_v2_read(token,offset,data,count);
    else
#endif
        err = is_live ? mesh_diag_live_read(token, offset, data, count) : diag_store_read(offset, data, count);
    if (err != ESP_OK) {
        reply_status(node_id, command, (is_live || is_v2) ? "stale_token" : "storage_error"); return true;
    }
    static const char hex[] = "0123456789abcdef";
    char encoded[DIAG_USB_MAX_READ * 2 + 1];
    for (size_t i = 0; i < count; ++i) {
        encoded[i * 2] = hex[data[i] >> 4];
        encoded[i * 2 + 1] = hex[data[i] & 15];
    }
    encoded[count * 2] = 0;
    (void)reply("{\"status\":\"ok\",\"protocol_version\":2,\"node_id\":\"%s\",\"command\":\"diag_read\","
                "\"offset\":%" PRIu32 ",\"token\":%" PRIu32 ",\"data\":\"%s\",\"crc32\":\"%08" PRIx32 "\"}\n",
                node_id, offset, token, encoded, diag_store_crc32(data, count));
    return true;
}
#else
bool diag_usb_handle(const cJSON *request, const char *command, const char *node_id) {
    (void)request; (void)command; (void)node_id; return false;
}
#endif
