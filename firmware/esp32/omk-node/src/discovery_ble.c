#include "discovery_ble.h"
#include "node_protocol.h"
#include "switchbot_relay.h"

#include <stdio.h>
#include <string.h>
#include "esp_bt.h"
#include "esp_log.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"

static const char *TAG = "omk_discovery_ble";
static const uint8_t SERVICE_UUID_LE[16] = {0x01,0x51,0x7b,0x7d,0xe7,0x95,0x37,0x9f,0x3a,0x4e,0x64,0x7b,0x90,0x4d,0x2a,0x7d};
static bool controller_started;
static bool bluedroid_started;
static bool scan_started;
/* ESP-IDF GAP API accepts a non-const pointer for these immutable settings. */
static esp_ble_adv_params_t discovery_adv_params = {
    .adv_int_min = 0x80,
    .adv_int_max = 0xa0,
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};
/* Use ESP-IDF's documented default interval/window (10 ms each) for a
 * continuous passive observation window. This Node never connects to or
 * pairs with the observed devices. */
static esp_ble_scan_params_t switchbot_scan_params = {
    .scan_type = BLE_SCAN_TYPE_PASSIVE,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .scan_filter_policy = BLE_SCAN_FILTER_ALLOW_ALL,
    .scan_interval = 0x0010,
    .scan_window = 0x0010,
    .scan_duplicate = BLE_SCAN_DUPLICATE_DISABLE,
};

static esp_err_t discovery_start_advertising(void) {
    esp_err_t err = esp_ble_gap_start_advertising(&discovery_adv_params);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start discovery advertising: %s", esp_err_to_name(err));
    }
    return err;
}

static void log_switchbot_advertisement(const esp_ble_gap_cb_param_t *param) {
    const uint8_t *advertising_data = param->scan_rst.ble_adv;
    uint8_t manufacturer_length = 0;
    uint8_t *manufacturer_data = esp_ble_resolve_adv_data_by_type(
        (uint8_t *)advertising_data, param->scan_rst.adv_data_len,
        ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE, &manufacturer_length);
    if (manufacturer_data == NULL || manufacturer_length < 2 ||
        manufacturer_data[0] != 0x69 || manufacturer_data[1] != 0x09) {
        return;
    }

    char payload_hex[ESP_BLE_ADV_DATA_LEN_MAX * 2 + 1] = {0};
    size_t offset = 0;
    for (uint8_t index = 0; index < manufacturer_length &&
                            offset + 2 < sizeof(payload_hex); ++index) {
        offset += (size_t)snprintf(payload_hex + offset,
                                   sizeof(payload_hex) - offset,
                                   "%02x", manufacturer_data[index]);
    }
    ESP_LOGD(TAG, "SwitchBot advertisement addr=" ESP_BD_ADDR_STR
                  " rssi=%d len=%u data=%s",
             ESP_BD_ADDR_HEX(param->scan_rst.bda), param->scan_rst.rssi,
             manufacturer_length, payload_hex);

    /* ESP-IDF returns the Company ID as the first two manufacturer bytes;
     * the existing Gateway decoder receives the following device layout. */
    switchbot_relay_handle_manufacturer_data(manufacturer_data + 2,
                                             manufacturer_length - 2);
}

static void gap_callback(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    if (event == ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Discovery raw advertising data configured status=%d", param->adv_data_raw_cmpl.status);
        discovery_start_advertising();
    } else if (event == ESP_GAP_BLE_ADV_START_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Discovery advertising start complete status=%d", param->adv_start_cmpl.status);
        if (param->adv_start_cmpl.status == ESP_BT_STATUS_SUCCESS) {
            esp_err_t err = esp_ble_gap_set_scan_params(&switchbot_scan_params);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "Failed to configure passive SwitchBot scan: %s",
                         esp_err_to_name(err));
            }
        }
    } else if (event == ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Discovery advertising stop complete status=%d", param->adv_stop_cmpl.status);
    } else if (event == ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT) {
        if (param->scan_param_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Passive SwitchBot scan configuration failed status=%d",
                     param->scan_param_cmpl.status);
            return;
        }
        esp_err_t err = esp_ble_gap_start_scanning(0);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to start passive SwitchBot scan: %s",
                     esp_err_to_name(err));
            return;
        }
    } else if (event == ESP_GAP_BLE_SCAN_START_COMPLETE_EVT) {
        scan_started = param->scan_start_cmpl.status == ESP_BT_STATUS_SUCCESS;
        ESP_LOGI(TAG, "Passive SwitchBot scan start complete status=%d",
                 param->scan_start_cmpl.status);
    } else if (event == ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Passive SwitchBot scan stop complete status=%d",
                 param->scan_stop_cmpl.status);
    } else if (event == ESP_GAP_BLE_SCAN_RESULT_EVT &&
               param->scan_rst.search_evt == ESP_GAP_SEARCH_INQ_RES_EVT) {
        log_switchbot_advertisement(param);
    }
}

esp_err_t discovery_ble_start(uint64_t id, uint8_t provisioning_state) {
    uint8_t packet[31] = {0x02,0x01,0x06,0x1b,0x21};
    memcpy(packet + 5, SERVICE_UUID_LE, sizeof(SERVICE_UUID_LE));
    packet[21]=OMK_NODE_PROTOCOL_VERSION; packet[22]=provisioning_state;
    packet[23]=(OMK_NODE_CAPABILITIES >> 8) & 0xff;
    packet[24]=OMK_NODE_CAPABILITIES & 0xff;
    for (int i=0;i<6;++i) packet[25+i]=(id>>(40-8*i))&0xff;
    esp_err_t err;
    esp_bt_controller_config_t config=BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    if ((err=esp_bt_controller_init(&config)) != ESP_OK) return err;
    controller_started=true;
    if ((err=esp_bt_controller_enable(ESP_BT_MODE_BLE)) != ESP_OK) goto fail;
    if ((err=esp_bluedroid_init()) != ESP_OK) goto fail;
    bluedroid_started=true;
    if ((err=esp_bluedroid_enable()) != ESP_OK) goto fail;
    if ((err=esp_ble_gap_register_callback(gap_callback)) != ESP_OK) goto fail;
    if ((err=esp_ble_gap_config_adv_data_raw(packet,sizeof(packet))) != ESP_OK) goto fail;
    return ESP_OK;
fail:
    discovery_ble_stop();
    return err;
}

esp_err_t discovery_ble_stop(void) {
    esp_err_t first = esp_ble_gap_stop_advertising();
    if (scan_started) {
        esp_err_t err = esp_ble_gap_stop_scanning();
        if (first == ESP_OK && err != ESP_OK) {
            first = err;
        }
        scan_started = false;
    }
    if (bluedroid_started) {
        esp_err_t err=esp_bluedroid_disable(); if (first==ESP_OK && err!=ESP_OK) first=err;
        err=esp_bluedroid_deinit(); if (first==ESP_OK && err!=ESP_OK) first=err;
        bluedroid_started=false;
    }
    if (controller_started) {
        esp_err_t err=esp_bt_controller_disable(); if (first==ESP_OK && err!=ESP_OK) first=err;
        err=esp_bt_controller_deinit(); if (first==ESP_OK && err!=ESP_OK) first=err;
        controller_started=false;
    }
    return first;
}
