#include "discovery_ble.c"
#include <stdlib.h>
static int64_t clock_us;
int64_t esp_timer_get_time(void) { return clock_us; }
void switchbot_relay_handle_observation(const uint8_t a[6], int r, const uint8_t *m,size_t ml,const uint8_t *s,size_t sl) { (void)a;(void)r;(void)m;(void)ml;(void)s;(void)sl;observations++; }
static void event(int e,int status) { esp_ble_gap_cb_param_t p={0};p.scan_param_cmpl.status=p.scan_start_cmpl.status=p.scan_stop_cmpl.status=status;gap_callback(e,&p); }
static void tick(void) {clock_us+=100000; discovery_ble_control_tick();}
static void ready(void) {assert(discovery_ble_start(1,1)==0);event(ESP_GAP_BLE_ADV_START_COMPLETE_EVT,0);tick();event(ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT,0);tick();}
static void started(void) {event(ESP_GAP_BLE_SCAN_START_COMPLETE_EVT,0);tick();}
static void stopped(void) {event(ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT,0);tick();}
static void observation(void) {esp_ble_gap_cb_param_t p={0};p.scan_rst.search_evt=ESP_GAP_SEARCH_INQ_RES_EVT;p.scan_rst.adv_data_len=5;uint8_t bytes[]={4,255,0x69,0x09,1};memcpy(p.scan_rst.ble_adv,bytes,5);gap_callback(ESP_GAP_BLE_SCAN_RESULT_EVT,&p);}
static void promote(void) {discovery_ble_set_mesh_role(true);}
static void demote(void) {discovery_ble_set_mesh_role(false);tick();}
int main(int argc,char **argv) {
 assert(argc==2);int c=atoi(argv[1]); ab_runtime_boot("020000000001","0123456789abcdef");
 if(c==0) {promote();ready();assert(starts==0);demote();event(ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT,0);tick();assert(starts==1);started();assert(accept_scan_result());}
 if(c==1) {ready();started();observation();assert(observations==1);promote();tick();assert(stops==1);observation();assert(observations==1);stopped();demote();assert(starts==2);started();observation();assert(observations==2);}
 if(c==2) {ready();promote();tick();assert(stops==0);started();assert(stops==1);stopped();assert(!accept_scan_result());}
 if(c==3) {ready();started();promote();tick();demote();assert(starts==1);stopped();assert(starts==2);started();assert(accept_scan_result());}
 if(c==4) {ready();for(int i=0;i<20;i++)demote();assert(starts==1);started();for(int i=0;i<20;i++){promote();tick();}assert(stops==1);stopped();promote();tick();assert(stops==1);}
 if(c==5) {ready();promote();started();stopped();assert(!accept_scan_result());demote();started();assert(accept_scan_result());}
 if(c==6) {api_error=1;ready();for(int i=0;i<30;i++)tick();assert(starts==1);api_error=0;promote();demote();for(int i=0;i<30;i++)tick();assert(starts==1); /* Missing restore PARAM callback stays latched. */}
 if(c==7) {ready();event(ESP_GAP_BLE_SCAN_START_COMPLETE_EVT,1);tick();event(ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT,0);tick();assert(starts==2);event(ESP_GAP_BLE_SCAN_START_COMPLETE_EVT,1);for(int i=0;i<50;i++)tick();assert(starts==2);}
 if(c==8) {ready();started();promote();tick();event(ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT,1);for(int i=0;i<50;i++){promote();tick();}assert(stops==1);observation();assert(observations==0);}
 if(c==9) {api_hook=promote;ready();api_hook=NULL;assert(starts==1);started();assert(stops==1);stopped();assert(!accept_scan_result());}
 assert(!adv_stops && !deinits);return 0;
}
