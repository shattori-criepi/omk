#include "discovery_ble.h"
#include "node_state.h"
#include "node_protocol.h"

#include <string.h>
#include "esp_bt.h"
#include "esp_log.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"

static const uint8_t SERVICE_UUID_LE[16] = {0x01,0x51,0x7b,0x7d,0xe7,0x95,0x37,0x9f,0x3a,0x4e,0x64,0x7b,0x90,0x4d,0x2a,0x7d};
static bool controller_started;
static bool bluedroid_started;
static uint16_t handles[3];
static const uint8_t CONTROL_SERVICE_UUID[16]={0x54,0x21,0x43,0x9a,0x01,0x7d,0x4a,0x88,0xb1,0x2f,0x68,0x42,0x91,0x70,0x34,0xc1};
static const uint8_t START_UUID[16]={0x55,0x21,0x43,0x9a,0x01,0x7d,0x4a,0x88,0xb1,0x2f,0x68,0x42,0x91,0x70,0x34,0xc1};
enum { CONTROL_SERVICE, START_DECL, START_VALUE, CONTROL_ATTR_COUNT };
static const uint16_t primary_service_uuid = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t character_declaration_uuid = ESP_GATT_UUID_CHAR_DECLARE;
static const uint8_t char_decl[]={ESP_GATT_CHAR_PROP_BIT_WRITE};
static const esp_gatts_attr_db_t control_db[CONTROL_ATTR_COUNT]={
 [CONTROL_SERVICE]={{ESP_GATT_AUTO_RSP},{ESP_UUID_LEN_16,(uint8_t*)&primary_service_uuid,ESP_GATT_PERM_READ,16,16,(uint8_t*)CONTROL_SERVICE_UUID}},
 [START_DECL]={{ESP_GATT_AUTO_RSP},{ESP_UUID_LEN_16,(uint8_t*)&character_declaration_uuid,ESP_GATT_PERM_READ,1,1,(uint8_t*)char_decl}},
 [START_VALUE]={{ESP_GATT_RSP_BY_APP},{ESP_UUID_LEN_128,(uint8_t*)START_UUID,ESP_GATT_PERM_WRITE,1,0,NULL}},};

static void gatts_callback(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *p) {
 if(event==ESP_GATTS_REG_EVT) esp_ble_gatts_create_attr_tab(control_db,gatts_if,CONTROL_ATTR_COUNT,0);
 else if(event==ESP_GATTS_CREAT_ATTR_TAB_EVT && p->add_attr_tab.status==ESP_GATT_OK){memcpy(handles,p->add_attr_tab.handles,sizeof(handles));esp_ble_gatts_start_service(handles[CONTROL_SERVICE]);}
 else if(event==ESP_GATTS_WRITE_EVT){
     esp_gatt_status_t s=ESP_GATT_WRITE_NOT_PERMIT;
     if(p->write.handle==handles[START_VALUE]) {
         if(p->write.is_prep) s=ESP_GATT_WRITE_NOT_PERMIT;
         else if(p->write.len!=1) s=ESP_GATT_INVALID_ATTR_LEN;
         else if(p->write.value[0]!=1) s=ESP_GATT_INVALID_PDU;
         else if(!node_state_post_event(NODE_EVENT_START_PROVISIONING)) s=ESP_GATT_BUSY;
         else s=ESP_GATT_OK;
     }
     if(p->write.need_rsp) esp_ble_gatts_send_response(gatts_if,p->write.conn_id,p->write.trans_id,s,NULL);
 }
}

static void gap_callback(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    (void)param;
    if (event != ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT) return;
    esp_ble_adv_params_t params = {.adv_int_min=0x80,.adv_int_max=0xa0,.adv_type=ADV_TYPE_IND,.own_addr_type=BLE_ADDR_TYPE_PUBLIC,.channel_map=ADV_CHNL_ALL,.adv_filter_policy=ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY};
    esp_ble_gap_start_advertising(&params);
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
    if ((err=esp_ble_gatts_register_callback(gatts_callback)) != ESP_OK) goto fail;
    if ((err=esp_ble_gatts_app_register(0)) != ESP_OK) goto fail;
    if ((err=esp_ble_gap_config_adv_data_raw(packet,sizeof(packet))) != ESP_OK) goto fail;
    return ESP_OK;
fail:
    discovery_ble_stop();
    return err;
}

esp_err_t discovery_ble_stop(void) {
    esp_err_t first = esp_ble_gap_stop_advertising();
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
