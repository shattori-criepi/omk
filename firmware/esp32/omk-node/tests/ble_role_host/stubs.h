#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <assert.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_BT_STATUS_SUCCESS 0
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define portMUX_TYPE int
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define ADV_TYPE_IND 0
#define BLE_ADDR_TYPE_PUBLIC 0
#define ADV_CHNL_ALL 7
#define ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY 0
#define BLE_SCAN_TYPE_ACTIVE 1
#define BLE_SCAN_FILTER_ALLOW_ALL 0
#define BLE_SCAN_DUPLICATE_DISABLE 0
#define ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE 255
#define ESP_BT_MODE_BLE 1
typedef struct { int adv_int_min,adv_int_max,adv_type,own_addr_type,channel_map,adv_filter_policy; } esp_ble_adv_params_t;
typedef struct { int scan_type,own_addr_type,scan_filter_policy,scan_interval,scan_window,scan_duplicate; } esp_ble_scan_params_t;
typedef int esp_bt_controller_config_t;
#define BT_CONTROLLER_INIT_CONFIG_DEFAULT() 0
typedef enum { ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT, ESP_GAP_BLE_ADV_START_COMPLETE_EVT, ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT, ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT, ESP_GAP_BLE_SCAN_START_COMPLETE_EVT, ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT, ESP_GAP_BLE_SCAN_RESULT_EVT } esp_gap_ble_cb_event_t;
#define ESP_GAP_SEARCH_INQ_RES_EVT 1
typedef struct { struct { int status; } adv_data_raw_cmpl,adv_start_cmpl,adv_stop_cmpl,scan_param_cmpl,scan_start_cmpl,scan_stop_cmpl; struct { uint8_t ble_adv[62],bda[6]; int adv_data_len,scan_rsp_len,rssi,search_evt; } scan_rst; } esp_ble_gap_cb_param_t;
static int starts,stops,adv_stops,deinits,observations,api_error;
static void (*api_hook)(void);
static int esp_ble_gap_start_scanning(uint32_t duration) { assert(duration==0);starts++;if(api_hook)api_hook();return api_error; }
static int esp_ble_gap_stop_scanning(void) { stops++;if(api_hook)api_hook();return api_error; }
static int esp_ble_gap_start_advertising(esp_ble_adv_params_t *p) { (void)p;return 0; }
static int esp_ble_gap_stop_advertising(void) {adv_stops++;return 0;}
static int esp_ble_gap_set_scan_params(esp_ble_scan_params_t *p) {assert(p->scan_interval==160 && p->scan_window==80 && p->scan_type==BLE_SCAN_TYPE_ACTIVE);return 0;}
static int esp_bt_controller_init(esp_bt_controller_config_t *p) {(void)p;return 0;}
static int esp_bt_controller_enable(int p) {(void)p;return 0;}
static int esp_bluedroid_init(void) {return 0;}
static int esp_bluedroid_enable(void) {return 0;}
static int esp_bluedroid_disable(void) {deinits++;return 0;}
static int esp_bluedroid_deinit(void) {deinits++;return 0;}
static int esp_bt_controller_disable(void) {deinits++;return 0;}
static int esp_bt_controller_deinit(void) {deinits++;return 0;}
static int esp_ble_gap_register_callback(void (*p)(esp_gap_ble_cb_event_t,esp_ble_gap_cb_param_t*)) {(void)p;return 0;}
static int esp_ble_gap_config_adv_data_raw(uint8_t *p,int n) {(void)p;(void)n;return 0;}
