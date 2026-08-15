#include "node_state.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "nvs.h"

#define NODE_STATE_QUEUE_DEPTH 4
#define NODE_STATE_TASK_STACK_SIZE 2048
#define NODE_STATE_TASK_PRIORITY 5

static const char *TAG = "omk-node-state";
static QueueHandle_t event_queue;

static bool provisioning_pop_is_valid(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "START_PROVISIONING rejected: provisioning PoP is unavailable");
        return false;
    }

    size_t pop_length = 0;
    err = nvs_get_blob(nvs, NODE_NVS_PROVISIONING_POP_KEY, NULL, &pop_length);
    nvs_close(nvs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "START_PROVISIONING rejected: provisioning PoP is missing");
        return false;
    }
    if (pop_length != NODE_PROVISIONING_POP_LENGTH) {
        ESP_LOGW(TAG, "START_PROVISIONING rejected: provisioning PoP has invalid length");
        return false;
    }
    return true;
}

static esp_err_t request_provisioning_boot(void) {
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NODE_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_u8(nvs, NODE_NVS_NEXT_BOOT_MODE_KEY,
                     NODE_BOOT_MODE_PROVISIONING);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

static void node_state_task(void *arg) {
    (void)arg;
    node_state_t state = NODE_STATE_DISCOVERY;
    node_event_t event;

    for (;;) {
        if (xQueueReceive(event_queue, &event, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        if (event != NODE_EVENT_START_PROVISIONING || state != NODE_STATE_DISCOVERY) {
            continue;
        }

        ESP_LOGI(TAG, "START_PROVISIONING event received in state task");
        if (!provisioning_pop_is_valid()) {
            continue;
        }

        state = NODE_STATE_PROVISIONING_BOOT_PENDING;
        esp_err_t err = request_provisioning_boot();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to save provisioning boot request: %s",
                     esp_err_to_name(err));
            state = NODE_STATE_DISCOVERY;
            continue;
        }

        ESP_LOGI(TAG, "Provisioning boot request saved; restarting");
        esp_restart();
    }
}

esp_err_t node_state_start(void) {
    if (event_queue != NULL) {
        return ESP_OK;
    }

    event_queue = xQueueCreate(NODE_STATE_QUEUE_DEPTH, sizeof(node_event_t));
    if (event_queue == NULL) {
        return ESP_ERR_NO_MEM;
    }

    if (xTaskCreate(node_state_task, "node_state", NODE_STATE_TASK_STACK_SIZE,
                    NULL, NODE_STATE_TASK_PRIORITY, NULL) != pdPASS) {
        vQueueDelete(event_queue);
        event_queue = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool node_state_post_event(node_event_t event) {
    return event_queue != NULL && xQueueSend(event_queue, &event, 0) == pdTRUE;
}
