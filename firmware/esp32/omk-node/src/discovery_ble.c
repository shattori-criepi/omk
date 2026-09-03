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
/* Active scanning sends scan requests only to obtain scan responses.  This
 * Node never connects to or pairs with the observed devices. */
static esp_ble_scan_params_t switchbot_scan_params = {
    .scan_type = BLE_SCAN_TYPE_ACTIVE,
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
    const uint8_t *manufacturer_data = NULL, *service_data = NULL;
    size_t manufacturer_length = 0, service_length = 0, offset = 0;
    size_t advertising_length = param->scan_rst.adv_data_len + param->scan_rst.scan_rsp_len;
    /* Parse AD structures once so that a combined manufacturer/service packet
     * (notably Presence Sensor Pro) remains intact for the Gateway decoder. */
    while (offset < advertising_length) {
        uint8_t field_length = advertising_data[offset++];
        if (field_length == 0 || offset + field_length > advertising_length) break;
        uint8_t type = advertising_data[offset++];
        const uint8_t *value = advertising_data + offset;
        size_t value_length = field_length - 1;
        if (type == ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE && value_length >= 2 &&
            value[0] == 0x69 && value[1] == 0x09) {
            manufacturer_data = value + 2; manufacturer_length = value_length - 2;
        } else if (type == 0x16 && value_length >= 2 && value[0] == 0x3d && value[1] == 0xfd) {
            service_data = value + 2; service_length = value_length - 2;
        }
        offset += value_length;
    }
    if (manufacturer_data == NULL && service_data == NULL) return;
    switchbot_relay_handle_observation(param->scan_rst.bda, param->scan_rst.rssi,
                                       manufacturer_data, manufacturer_length,
                                       service_data, service_length);
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
                ESP_LOGE(TAG, "Failed to configure active SwitchBot scan: %s",
                         esp_err_to_name(err));
            }
        }
    } else if (event == ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Discovery advertising stop complete status=%d", param->adv_stop_cmpl.status);
    } else if (event == ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT) {
        if (param->scan_param_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Active SwitchBot scan configuration failed status=%d",
                     param->scan_param_cmpl.status);
            return;
        }
        esp_err_t err = esp_ble_gap_start_scanning(0);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to start active SwitchBot scan: %s",
                     esp_err_to_name(err));
            return;
        }
    } else if (event == ESP_GAP_BLE_SCAN_START_COMPLETE_EVT) {
        scan_started = param->scan_start_cmpl.status == ESP_BT_STATUS_SUCCESS;
        ESP_LOGI(TAG, "Active SwitchBot scan start complete status=%d",
                 param->scan_start_cmpl.status);
    } else if (event == ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT) {
        ESP_LOGI(TAG, "Active SwitchBot scan stop complete status=%d",
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
