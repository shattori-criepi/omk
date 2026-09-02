#include "usb_provisioning.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "wifi_station.h"

#define USB_PROVISIONING_PROTOCOL_VERSION 1
#define USB_PROVISIONING_LINE_MAX 256
#define USB_PROVISIONING_REBOOT_DELAY_MS 750

static const char *TAG = "omk-usb-provisioning";
static uint64_t provision_node_id;
static bool reboot_scheduled;

static void write_response(const char *response) {
    /* Responses are single JSON lines, so console logs can safely be ignored by
     * a host which only accepts complete JSON objects. */
    (void)usb_serial_jtag_write_bytes(response, strlen(response), pdMS_TO_TICKS(100));
    (void)usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(100));
}

static void write_status(const char *status) {
    char response[96];
    int length = snprintf(response, sizeof(response),
                          "{\"status\":\"%s\",\"protocol_version\":%u,"
                          "\"node_id\":\"%012" PRIx64 "\"}",
                          status, USB_PROVISIONING_PROTOCOL_VERSION,
                          provision_node_id & UINT64_C(0x0000ffffffffffff));
    if (length > 0 && length < (int)sizeof(response)) {
        write_response(response);
    }
}

static void reboot_task(void *argument) {
    (void)argument;
    vTaskDelay(pdMS_TO_TICKS(USB_PROVISIONING_REBOOT_DELAY_MS));
    ESP_LOGI(TAG, "USB provisioning request accepted; restarting into normal boot");
    esp_restart();
}

static bool schedule_reboot(void) {
    if (reboot_scheduled) {
        return false;
    }
    reboot_scheduled = true;
    if (xTaskCreate(reboot_task, "usb_prov_reboot", 2048, NULL, 5, NULL) != pdPASS) {
        reboot_scheduled = false;
        return false;
    }
    return true;
}

static void handle_line(char *line) {
    cJSON *root = cJSON_Parse(line);
    if (root == NULL) {
        write_status("invalid_request");
        return;
    }
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "protocol_version");
    const cJSON *command = cJSON_GetObjectItemCaseSensitive(root, "command");
    bool valid_header = cJSON_IsNumber(version) &&
                        version->valuedouble == USB_PROVISIONING_PROTOCOL_VERSION &&
                        cJSON_IsString(command) && command->valuestring != NULL;
    if (!valid_header) {
        cJSON_Delete(root);
        write_status("invalid_request");
        return;
    }
    if (strcmp(command->valuestring, "identify") == 0) {
        cJSON_Delete(root);
        write_status("ok");
        return;
    }
    if (reboot_scheduled) {
        cJSON_Delete(root);
        write_status("busy");
        return;
    }
    if (strcmp(command->valuestring, "clear_wifi") == 0) {
        cJSON_Delete(root);
        esp_err_t err = wifi_station_clear_saved_credentials();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "USB Gateway Wi-Fi credential clear failed: %s", esp_err_to_name(err));
            write_status("storage_error");
            return;
        }
        if (!schedule_reboot()) {
            ESP_LOGE(TAG, "Could not schedule USB credential-clear reboot");
            write_status("restart_error");
            return;
        }
        write_status("accepted");
        return;
    }
    if (strcmp(command->valuestring, "set_wifi") != 0) {
        cJSON_Delete(root);
        write_status("invalid_request");
        return;
    }
    const cJSON *ssid = cJSON_GetObjectItemCaseSensitive(root, "ssid");
    const cJSON *password = cJSON_GetObjectItemCaseSensitive(root, "password");
    bool valid = cJSON_IsString(ssid) && ssid->valuestring != NULL &&
                 cJSON_IsString(password) && password->valuestring != NULL;
    size_t ssid_length = valid ? strlen(ssid->valuestring) : 0;
    size_t password_length = valid ? strlen(password->valuestring) : 0;
    valid = valid && ssid_length > 0 &&
            ssid_length <= sizeof(((wifi_config_t *)0)->sta.ssid) &&
            password_length >= 8 &&
            password_length <= sizeof(((wifi_config_t *)0)->sta.password);
    if (!valid) {
        cJSON_Delete(root);
        write_status("invalid_request");
        return;
    }
    esp_err_t err = wifi_station_save_credentials((const uint8_t *)ssid->valuestring,
                                                  ssid_length,
                                                  (const uint8_t *)password->valuestring,
                                                  password_length);
    /* cJSON owns the only credential-bearing request buffer. Never log it. */
    cJSON_Delete(root);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "USB Wi-Fi credential save failed: %s", esp_err_to_name(err));
        write_status("storage_error");
        return;
    }
    if (!schedule_reboot()) {
        ESP_LOGE(TAG, "Could not schedule USB provisioning reboot");
        write_status("restart_error");
        return;
    }
    write_status("accepted");
}

static void usb_provisioning_task(void *argument) {
    (void)argument;
    char line[USB_PROVISIONING_LINE_MAX + 1];
    size_t used = 0;
    while (true) {
        uint8_t byte;
        int read = usb_serial_jtag_read_bytes(&byte, 1, pdMS_TO_TICKS(250));
        if (read != 1) {
            continue;
        }
        if (byte == '\r') {
            continue;
        }
        if (byte == '\n') {
            line[used] = '\0';
            if (used != 0) {
                handle_line(line);
            }
            used = 0;
        } else if (used < USB_PROVISIONING_LINE_MAX) {
            line[used++] = (char)byte;
        } else {
            used = 0;
            write_status("invalid_request");
        }
    }
}

esp_err_t usb_provisioning_start(uint64_t node_id) {
    provision_node_id = node_id;
    reboot_scheduled = false;
    usb_serial_jtag_driver_config_t config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    config.rx_buffer_size = USB_PROVISIONING_LINE_MAX;
    config.tx_buffer_size = USB_PROVISIONING_LINE_MAX;
    esp_err_t err = usb_serial_jtag_driver_install(&config);
    if (err != ESP_OK) {
        return err;
    }
    if (xTaskCreate(usb_provisioning_task, "usb_provisioning", 4096, NULL, 5, NULL) != pdPASS) {
        (void)usb_serial_jtag_driver_uninstall();
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "USB Serial/JTAG provisioning ready");
    return ESP_OK;
}
