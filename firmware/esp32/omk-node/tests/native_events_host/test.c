#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "mesh_transport.c"
static bool ble_root;
void discovery_ble_set_mesh_role(bool root) { ble_root = root; }

struct test_queue { unsigned count, capacity, head; size_t size; unsigned char *bytes; };
static esp_netif_t test_netif, foreign_netif;
static struct netif test_lwip_netif;
static esp_netif_ip_info_t live_ip;
static esp_netif_ip_info_t cached_ip;
static bool lease_bound, link_up = true, tcpip_context;
static void (*observation_hook)(void), (*ip_read_hook)(void), (*dhcp_stop_hook)(void);
static int tcpip_error, stop_error, start_error;
static unsigned got_ip_posts;
static unsigned lease_observations, delay_count;
static int64_t test_time = 1000000;
static void (*delay_hook)(void);
static esp_netif_dhcp_status_t dhcp = ESP_NETIF_DHCP_STOPPED;
static bool sdk_root, netif_up = true, conflicts = true, wrong_readback, race_epoch;
static int policy_error, ip_error, dhcp_error, send_error, mac_error;
static unsigned mesh_starts, dhcp_starts, dhcp_stops, mqtt_starts, mqtt_stops, mqtt_destroys, recv_calls;
static bool wifi_started, mesh_initialized, policy_verified, config_set;
static esp_event_handler_t mesh_callback, ip_callback, mqtt_callback;
static void *mqtt_arg;
static int fake_client;
static jmp_buf worker_exit;
static jmp_buf receipt_receive_exit;
static bool stop_on_delay;
static bool self_organized=true;
static int vote_error, reselect_error, connect_error, mqtt_disconnect_error;
static unsigned vote_calls, reselect_calls, connect_calls, mqtt_disconnects;
static void (*self_organized_hook)(void), (*disconnect_hook)(void);
static char parent_recovery_log[160];
static int last_enqueue_qos=-1;
static char last_topic[128];
static void (*role_query_hook)(void);
static void (*send_hook)(void), (*time_hook)(void);
static bool stop_sender_idle;
static unsigned send_calls;
static mesh_wire_message_t sent_message, injected_message;
static uint8_t sent_destination[6], injected_source[6];
static bool injected_receive, injected_pending;
static int mqtt_outbox_size;
static void (*log_hook)(void), (*parent_query_hook)(void);
static int sdk_layer_override=-2;
static int parent_query_error;
static uint8_t sdk_parent_override[6];

const char *esp_err_to_name(int error) { return "test-error"; }
void test_log(const char *tag, const char *format, ...) {
    if(log_hook && strstr(format,"Child uplink stalled:")) {
        void (*hook)(void)=log_hook;log_hook=NULL;hook();
    }
    if (!strstr(format, "Parent after recovery:")) return;
    va_list args; va_start(args, format);
    vsnprintf(parent_recovery_log, sizeof(parent_recovery_log), format, args);
    va_end(args);
}
int64_t esp_timer_get_time(void) { if(time_hook){void (*hook)(void)=time_hook;time_hook=NULL;hook();}return test_time; }
uint32_t esp_random(void) { static uint32_t value=42; return ++value; }
QueueHandle_t xQueueCreate(unsigned count, size_t size) {
    QueueHandle_t q=calloc(1,sizeof(*q)); assert(q);
    q->bytes=calloc(count,size); assert(q->bytes); q->capacity=count; q->size=size; return q;
}
int xQueueSend(QueueHandle_t q,const void *value,unsigned ticks) {
    if(q->count==q->capacity) return 0;
    memcpy(q->bytes+((q->head+q->count)%q->capacity)*q->size,value,q->size); ++q->count; return pdTRUE;
}
int xQueueOverwrite(QueueHandle_t q,const void *value) {
    assert(q->capacity==1);memcpy(q->bytes,value,q->size);q->head=0;q->count=1;return pdTRUE;
}
int xQueueReceive(QueueHandle_t q,void *value,unsigned ticks) {
    if (stop_sender_idle && q==local_data && ticks && !q->count) longjmp(worker_exit,1);
    if(!q->count) return 0;
    memcpy(value,q->bytes+q->head*q->size,q->size); q->head=(q->head+1)%q->capacity; --q->count; return pdTRUE;
}
void xQueueReset(QueueHandle_t q) { q->count=q->head=0; }
int xTaskCreate(void (*fn)(void *),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *handle) {
    *handle=&fake_client; return pdPASS;
}
void vTaskDelay(unsigned ticks) {
    if(stop_sender_idle) longjmp(worker_exit,1);
    if(stop_on_delay) longjmp(worker_exit,1);
    if(delay_hook) { test_time+=(int64_t)ticks*1000; ++delay_count; delay_hook(); }
}
esp_err_t esp_event_handler_register(esp_event_base_t base,int32_t id,esp_event_handler_t fn,void *arg) {
    if(base==MESH_EVENT) mesh_callback=fn;
    else if(base==IP_EVENT) ip_callback=fn;
    else assert(!"unexpected event registration");
    return ESP_OK;
}
static unsigned disconnect_calls, disable_calls, restore_calls;
static int disconnect_error, restore_error, subtree_nodes=1;
esp_err_t esp_mesh_set_self_organized(bool enable,bool select_parent) {
    assert(enable || !select_parent);
    if(!enable) ++disable_calls;
    if(enable && !select_parent) { ++restore_calls; if(restore_error) return restore_error; }
    self_organized=enable;
    if(select_parent) { assert(!sdk_root); ++reselect_calls; if(reselect_error) return reselect_error; }
    if(self_organized_hook) { void (*hook)(void)=self_organized_hook; self_organized_hook=NULL; hook(); }
    return ESP_OK;
}
esp_err_t esp_mesh_connect(void) { assert(self_organized); ++connect_calls; return connect_error; }
esp_err_t esp_mesh_disconnect(void) { assert(!sdk_root && !self_organized && !sdk_busy); ++disconnect_calls; return disconnect_error; }
bool esp_mesh_get_self_organized(void) { return self_organized; }
int esp_mesh_get_routing_table_size(void) { return subtree_nodes; }
esp_err_t esp_mesh_waive_root(const void *vote,int reason) {
    assert(sdk_root && self_organized && !vote && reason==MESH_VOTE_REASON_ROOT_INITIATED);
    ++vote_calls; return vote_error;
}
esp_err_t esp_read_mac(uint8_t *mac,int type) {
    memset(mac,0,6); mac[0]=2; mac[5]=(type==ESP_MAC_WIFI_STA ? 10 : 11); return mac_error;
}
esp_err_t node_identity_get_id_hex(char *out,size_t size) { snprintf(out,size,"020000000041"); return ESP_OK; }
esp_err_t gateway_credentials_load(omk_gateway_credentials_t *out) {
    memset(out,0,sizeof(*out)); memcpy(out->ssid,"test",4); out->ssid_length=4; out->psk_length=8; return ESP_OK;
}
esp_err_t mesh_credentials_derive(const uint8_t *ssid,size_t ssid_length,const uint8_t *psk,size_t psk_length,uint8_t id[6],char password[33]) {
    memset(id,2,6); memset(password,'a',32); password[32]=0; return ESP_OK;
}
void node_messages_handle_config(const char *payload,size_t length) {}
esp_err_t esp_wifi_set_ps(int value) { assert(!mesh_starts); return ESP_OK; }
esp_err_t esp_wifi_start(void) { wifi_started=true; return ESP_OK; }
esp_err_t esp_mesh_init(void) { assert(wifi_started); mesh_initialized=true; return ESP_OK; }
esp_err_t esp_mesh_allow_root_conflicts(bool allowed) {
    assert(mesh_initialized && !mesh_starts && !allowed);
    if(!policy_error) conflicts=allowed;
    return policy_error;
}
bool esp_mesh_is_root_conflicts_allowed(void) {
    assert(mesh_initialized && !policy_error); policy_verified=true;
    return wrong_readback || conflicts;
}
esp_err_t esp_mesh_set_max_layer(int layer) { assert(policy_verified && !conflicts); return ESP_OK; }
esp_err_t esp_mesh_set_ap_assoc_expire(int seconds) { assert(seconds==30); return ESP_OK; }
esp_err_t esp_mesh_send_block_time(uint32_t ms) { assert(ms==5000 && !mesh_starts); return ESP_OK; }
esp_err_t esp_mesh_set_ap_authmode(int mode) { return ESP_OK; }
esp_err_t esp_mesh_set_config(const mesh_cfg_t *config) {
    assert(policy_verified && !conflicts); config_set=true; return ESP_OK;
}
esp_err_t esp_mesh_start(void) {
    assert(config_set && policy_verified && !conflicts); ++mesh_starts; return ESP_OK;
}
bool esp_mesh_is_root(void) {
    if(role_query_hook) { void (*hook)(void)=role_query_hook; role_query_hook=NULL; hook(); }
    return sdk_root;
}
int esp_mesh_get_layer(void) {
    return sdk_layer_override!=-2 ? sdk_layer_override : state.session.parent_connected ? (sdk_root ? 1 : 3) : -1;
}
esp_err_t esp_mesh_get_parent_bssid(mesh_addr_t *parent) {
    if(parent_query_hook) { void (*hook)(void)=parent_query_hook;parent_query_hook=NULL;hook(); }
    if(parent_query_error) return parent_query_error;
    if(esp_mesh_get_layer()<=0) return ESP_FAIL;
    memcpy(parent->addr,sdk_parent_override[0] ? sdk_parent_override : state.status.parent_mac,6);
    return ESP_OK;
}
esp_err_t esp_mesh_post_toDS_state(bool reachable) { return ESP_OK; }
esp_err_t esp_mesh_send(const mesh_addr_t *to,const mesh_data_t *data,int flags,const void *opt,int count) {
    /* IDF 6.0.1 rejects application DEF data on an upstream route.
     * This harness models child->Root and Root->descendant routes only;
     * arbitrary peer route discovery and SDK resource queues are not simulated. */
    if ((!to || !sdk_root || (flags & MESH_DATA_TODS)) && data->tos != MESH_TOS_P2P)
        return ESP_ERR_MESH_ARGUMENT;
    assert(mesh_wire_decode(data->data,data->size,&sent_message));
    if (sent_message.kind==MESH_MESSAGE_STATUS_RECEIPT || sent_message.kind==MESH_MESSAGE_CONFIG) {
        assert(to && data->tos==MESH_TOS_DEF && flags==(MESH_DATA_P2P|MESH_DATA_NONBLOCK));
    } else assert(data->tos==MESH_TOS_P2P);
    /* NONBLOCK is not an SDK rejection for P2P: it is ignored. The same
     * send_hook executes for this synchronous call regardless of that flag. */
    if(to) memcpy(sent_destination,to->addr,6);
    ++send_calls;
    if (send_hook) { void (*hook)(void)=send_hook; send_hook=NULL; hook(); }
    return send_error;
}
esp_err_t esp_mesh_recv(mesh_addr_t *from,mesh_data_t *data,int timeout,int *flags,void *opt,int count) {
    assert(timeout==500);
    if(injected_receive) {
        if(!injected_pending) longjmp(receipt_receive_exit,1);
        injected_pending=false;
        size_t length;
        assert(mesh_wire_encode(&injected_message,data->data,data->size,&length));
        data->size=length; data->proto=MESH_PROTO_BIN; memcpy(from->addr,injected_source,6);
        return ESP_OK;
    }
    if(++recv_calls==1) return ESP_ERR_MESH_TIMEOUT;
    if(recv_calls==2) return ESP_FAIL;
    if(recv_calls==3) { data->proto=MESH_PROTO_BIN; data->size=0; return ESP_OK; }
    longjmp(worker_exit,1);
}
esp_err_t esp_netif_create_default_wifi_mesh_netifs(esp_netif_t **sta,esp_netif_t **ap) {
    *sta=&test_netif; return ESP_OK;
}
esp_err_t esp_netif_dhcpc_start(esp_netif_t *netif) {
    /* IDF6.0.1 start resets cached/live IP before obtaining a new lease. */
    assert(netif==&test_netif); ++dhcp_starts;
    if(start_error) return start_error;
    memset(&live_ip,0,sizeof(live_ip)); memset(&cached_ip,0,sizeof(cached_ip)); lease_bound=false;
    dhcp=ESP_NETIF_DHCP_STARTED; return ESP_OK;
}
esp_err_t esp_netif_dhcpc_stop(esp_netif_t *netif) {
    assert(netif==&test_netif); ++dhcp_stops;
    if(dhcp_stop_hook) { void (*hook)(void)=dhcp_stop_hook; dhcp_stop_hook=NULL; hook(); }
    if(stop_error) return stop_error;
    dhcp=ESP_NETIF_DHCP_STOPPED; lease_bound=false; return ESP_OK;
}
esp_err_t esp_netif_tcpip_exec(esp_netif_callback_fn fn, void *arg) {
    assert(!tcpip_context);
    if(tcpip_error) return tcpip_error;
    if(observation_hook) { void (*hook)(void)=observation_hook; observation_hook=NULL; hook(); }
    ++lease_observations;
    tcpip_context=true; esp_err_t result=fn(arg); tcpip_context=false; return result;
}
void *esp_netif_get_netif_impl(esp_netif_t *netif) {
    assert(tcpip_context && netif==&test_netif); return &test_lwip_netif;
}
bool netif_is_link_up(const struct netif *netif) { assert(tcpip_context); return link_up; }
bool dhcp_supplied_address(const struct netif *netif) { assert(tcpip_context); return lease_bound; }
esp_err_t esp_netif_dhcpc_get_status(esp_netif_t *netif,esp_netif_dhcp_status_t *status) {
    *status=dhcp; return dhcp_error;
}
esp_err_t esp_netif_get_ip_info(esp_netif_t *netif,esp_netif_ip_info_t *info) {
    assert(tcpip_context);
    *info=live_ip;
    if(race_epoch) { race_epoch=false; mesh_session_invalidate(&state.session); }
    if(ip_read_hook) { void (*hook)(void)=ip_read_hook; ip_read_hook=NULL; hook(); }
    return ip_error;
}
bool esp_netif_is_netif_up(esp_netif_t *netif) { return netif_up; }
esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *config) { return &fake_client; }
esp_err_t esp_mqtt_client_register_event(esp_mqtt_client_handle_t client,int id,esp_event_handler_t fn,void *arg) {
    mqtt_callback=fn; mqtt_arg=arg; return ESP_OK;
}
esp_err_t esp_mqtt_client_start(esp_mqtt_client_handle_t client) { ++mqtt_starts; return ESP_OK; }
esp_err_t esp_mqtt_client_stop(esp_mqtt_client_handle_t client) { ++mqtt_stops; return ESP_OK; }
esp_err_t esp_mqtt_client_destroy(esp_mqtt_client_handle_t client) { ++mqtt_destroys; return ESP_OK; }
esp_err_t esp_mqtt_client_disconnect(esp_mqtt_client_handle_t client) {
    ++mqtt_disconnects;
    if(disconnect_hook) { void (*hook)(void)=disconnect_hook; disconnect_hook=NULL; hook(); }
    return mqtt_disconnect_error;
}
int esp_mqtt_client_subscribe(esp_mqtt_client_handle_t client,const char *topic,int qos) { return 1; }
int esp_mqtt_client_get_outbox_size(esp_mqtt_client_handle_t client) { return mqtt_outbox_size; }
int esp_mqtt_client_enqueue(esp_mqtt_client_handle_t client,const char *topic,const char *payload,int length,int qos,int retain,bool store) {
    snprintf(last_topic,sizeof(last_topic),"%s",topic);
    assert(qos==0 || qos==1); last_enqueue_qos=qos; return qos==1 ? 1 : 0;
}

static void event(int id,void *data) { mesh_callback(NULL,MESH_EVENT,id,data); }
static void connected(bool root) {
    sdk_root=root;
    mesh_event_connected_t e={.self_layer=root ? 1 : 3,.connected={.bssid={2,0,0,0,0,20}}};
    event(MESH_EVENT_PARENT_CONNECTED,&e);
}
static void rootless(bool value) {
    mesh_event_network_state_t e={.is_rootless=value}; event(MESH_EVENT_NETWORK_STATE,&e);
}
static ip_event_got_ip_t lease(void) {
    live_ip=(esp_netif_ip_info_t){.ip.addr=0x010032c0,.netmask.addr=0x00ffffff,.gw.addr=0x010032c0};
    lease_bound=true;
    return (ip_event_got_ip_t){.esp_netif=&test_netif,.ip_info=live_ip};
}
static void got_ip(ip_event_got_ip_t *e) { ip_callback(NULL,IP_EVENT,IP_EVENT_STA_GOT_IP,e); }
static void bridge_once(void) {
    stop_on_delay=true;
    if(!setjmp(worker_exit)) mqtt_bridge(NULL);
    stop_on_delay=false;
}
static void mqtt_connected(void) {
    esp_mqtt_event_t e={.client=&fake_client}; mqtt_callback(mqtt_arg,0,MQTT_EVENT_CONNECTED,&e);
}
static void check_rootless_ip(void) {
    connected(true); rootless(true);
    ip_event_got_ip_t e=lease(); got_ip(&e);
    assert(state.session.has_ip && !root_ready(&state));
    bridge_once(); assert(!mqtt_starts);
    unsigned before=dhcp_starts;
    rootless(false); assert(root_ready(&state));
    bridge_once(); mqtt_connected();
    assert(mqtt_starts==1 && state.session.mqtt_connected && dhcp_starts==before);
    mqtt_context_t stale=*(mqtt_context_t *)mqtt_arg;
    rootless(true);
    bridge_once(); assert(mqtt_stops==1 && mqtt_destroys==1 && state.session.has_ip);
    esp_mqtt_event_t late={.client=&fake_client};
    mqtt_event(&stale,0,MQTT_EVENT_CONNECTED,&late); assert(!state.session.mqtt_connected);
    rootless(false); bridge_once(); mqtt_connected(); assert(mqtt_starts==2 && state.session.mqtt_connected);
    mqtt_event(&stale,0,MQTT_EVENT_DISCONNECTED,&late); assert(state.session.mqtt_connected);
}
static void check_stale_ip(void) {
    connected(true); rootless(true); ip_event_got_ip_t valid=lease(), e=valid;
    e.esp_netif=&foreign_netif; got_ip(&e); assert(!state.session.has_ip);
    e=valid; ++e.ip_info.ip.addr; got_ip(&e); assert(!state.session.has_ip);
    e=valid; ++e.ip_info.netmask.addr; got_ip(&e); assert(!state.session.has_ip);
    e=valid; ++e.ip_info.gw.addr; got_ip(&e); assert(!state.session.has_ip);
    netif_up=false; got_ip(&valid); assert(!state.session.has_ip); netif_up=true;
    dhcp=ESP_NETIF_DHCP_STOPPED; got_ip(&valid); assert(!state.session.has_ip); dhcp=ESP_NETIF_DHCP_STARTED;
    dhcp_error=ESP_FAIL; got_ip(&valid); assert(!state.session.has_ip); dhcp_error=0;
    ip_error=ESP_FAIL; got_ip(&valid); assert(!state.session.has_ip); ip_error=0;
    sdk_root=false; got_ip(&valid); assert(!state.session.has_ip); sdk_root=true;
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL); got_ip(&valid); assert(!state.session.has_ip);
    connected(true); got_ip(&valid); assert(!state.session.has_ip); /* new DHCP, old address event */
    e=lease(); rootless(true); got_ip(&e); assert(state.session.has_ip);
    ip_callback(NULL,IP_EVENT,IP_EVENT_STA_LOST_IP,NULL); assert(state.session.has_ip); /* old LOST */
    memset(&live_ip,0,sizeof(live_ip));
    ip_callback(NULL,IP_EVENT,IP_EVENT_STA_LOST_IP,NULL); assert(!state.session.has_ip);
    rootless(false); assert(!root_ready(&state)); got_ip(&e); assert(!state.session.has_ip);
    e=lease(); got_ip(&e); assert(root_ready(&state));
    event(MESH_EVENT_STOPPED,NULL); got_ip(&e); assert(!state.session.has_ip);
}
static void check_epoch_race(void) {
    connected(true); rootless(true); ip_event_got_ip_t e=lease();
    race_epoch=true; got_ip(&e); assert(!state.session.has_ip);
    got_ip(&e); assert(state.session.has_ip); /* fresh observation of the current lease */
    uint32_t old=state.session.epoch;
    mesh_session_invalidate(&state.session);
    assert(!mesh_session_got_ip(&state.session,old));
}
static void check_role_switch(void) {
    connected(true); ip_event_got_ip_t e=lease(); got_ip(&e);
    bridge_once(); mqtt_connected(); assert(state.session.mqtt_connected);
    mqtt_context_t stale=*(mqtt_context_t *)mqtt_arg;
    connected(false); got_ip(&e); bridge_once();
    assert(!state.session.has_ip && !state.session.mqtt_connected && mqtt_destroys==1);
    connected(true); rootless(true); got_ip(&e); assert(!state.session.has_ip);
    e=lease(); got_ip(&e); rootless(false); bridge_once(); mqtt_connected();
    assert(state.session.mqtt_connected && mqtt_starts==2);
    esp_mqtt_event_t late={.client=&fake_client};
    mqtt_event(&stale,0,MQTT_EVENT_DISCONNECTED,&late); assert(state.session.mqtt_connected);
}
static void check_diagnostics(void) {
    mesh_transport_status_t s; const uint8_t zero[6]={0};
    mesh_transport_get_status(&s); assert(s.layer==-1 && s.self_ap_mac[5]==11 && !s.last_disconnect_valid);
    connected(false);
    mesh_event_root_address_t r={.addr={2,0,0,0,0,30}};
    event(MESH_EVENT_ROOT_ADDRESS,&r); event(MESH_EVENT_ROOT_ADDRESS,&r);
    mesh_transport_get_status(&s); assert(!s.root_change_count && s.root_mac[5]==30 && s.layer==3);
    r.addr[5]=31; event(MESH_EVENT_ROOT_ADDRESS,&r);
    mesh_event_disconnected_t d={.reason=200,.rssi=-84};
    event(MESH_EVENT_PARENT_DISCONNECTED,&d);
    mesh_transport_get_status(&s);
    assert(s.parent_disconnect_count==1 && s.root_change_count==1 && s.layer==-1);
    assert(s.last_disconnect_valid && s.last_disconnect_reason==200 && s.last_disconnect_rssi==-84);
    assert(!memcmp(s.parent_mac,zero,6) && !memcmp(s.root_mac,zero,6));
    connected(false); mesh_transport_get_status(&s); assert(s.last_disconnect_rssi==-84 && s.parent_connected);
    rootless(true); mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,zero,6) && s.parent_mac[5]==20);
    rootless(false); connected(true); mesh_transport_get_status(&s); assert(s.root_mac[5]==10);
    event(MESH_EVENT_STOPPED,NULL); mesh_transport_get_status(&s);
    assert(s.layer==-1 && !memcmp(s.parent_mac,zero,6) && !memcmp(s.root_mac,zero,6));
    assert(s.parent_disconnect_count==1 && s.last_disconnect_rssi==-84);
}
static void check_io_counters(void) {
    connected(false);
    mesh_wire_message_t m={.kind=MESH_MESSAGE_NODE_STATUS,.node_id="020000000041",.length=2,.payload="{}"};
    publication_t origin={.epoch=state.session.epoch,.local_generation=state.local_generation};
    assert(send_message(&m,NULL,&origin)==ESP_OK);
    send_error=ESP_ERR_MESH_TIMEOUT; assert(send_message(&m,NULL,&origin)==send_error);
    send_error=ESP_ERR_NO_MEM; assert(send_message(&m,NULL,&origin)==send_error);
    assert(state.status.mesh_tx_ok==1 && state.status.mesh_tx_errors==2);
    if(!setjmp(worker_exit)) mesh_receiver(NULL);
    assert(state.status.mesh_rx_ok==1 && state.status.mesh_rx_errors==1 && state.status.rx_dropped==1);
    for(int i=0;i<LOCAL_DATA_COUNT;i++) assert(mesh_transport_publish(MESH_MESSAGE_SEN66,"{}",2)==ESP_OK);
    assert(mesh_transport_publish(MESH_MESSAGE_SEN66,"{}",2)==ESP_ERR_NO_MEM);
    assert(state.status.tx_accepted==LOCAL_DATA_COUNT && state.status.tx_dropped==1);
}

/* Model the IDF6 Mesh netif boundary, not the application expectation:
 * DHCP_CLIENT flag is absent, so loss retains cached_ip and a same-tuple
 * acquisition does NOT post GOT_IP. No test below injects that missing event. */
static void sdk_acquire(esp_netif_ip_info_t info) {
    live_ip=info; lease_bound=true;
    if(memcmp(&cached_ip,&live_ip,sizeof(live_ip))) {
        cached_ip=live_ip; ++got_ip_posts;
        ip_event_got_ip_t e={.esp_netif=&test_netif,.ip_info=live_ip}; got_ip(&e);
    }
}
static void sdk_lost(void) {
    memset(&live_ip,0,sizeof(live_ip)); lease_bound=false;
    /* The default 120-second LOST_IP timer has elapsed. */
    ip_callback(NULL,IP_EVENT,IP_EVENT_STA_LOST_IP,NULL);
}
static void check_reacquisition(bool different, bool during_rootless) {
    connected(true); esp_netif_ip_info_t address=lease().ip_info;
    sdk_acquire(address); bridge_once(); mqtt_connected();
    assert(got_ip_posts==1 && state.session.mqtt_connected);
    unsigned starts=dhcp_starts, stops=dhcp_stops;
    mqtt_context_t stale=*(mqtt_context_t *)mqtt_arg;
    sdk_lost(); bridge_once();
    assert(!state.session.has_ip && !state.session.mqtt_connected && mqtt_destroys==1);
    if(during_rootless) rootless(true);
    if(different) ++address.ip.addr;
    sdk_acquire(address);
    assert(got_ip_posts==(different ? 2U : 1U));
    if(!different) assert(!state.session.has_ip); /* no fabricated event */
    bridge_once(); assert(state.session.has_ip);
    if(during_rootless) {
        assert(mqtt_starts==1 && !root_ready(&state));
        rootless(false); bridge_once();
    }
    mqtt_connected(); assert(mqtt_starts==2 && state.session.mqtt_connected);
    esp_mqtt_event_t late={.client=&fake_client};
    mqtt_event(&stale,0,MQTT_EVENT_DISCONNECTED,&late); assert(state.session.mqtt_connected);
    ip_callback(NULL,IP_EVENT,IP_EVENT_STA_LOST_IP,NULL); assert(state.session.has_ip);
    assert(dhcp_starts==starts && dhcp_stops==stops); /* observation never restarts DHCP */
    for(unsigned i=0;i<3;i++) {
        sdk_lost(); bridge_once(); rootless(true);
        sdk_acquire(address); bridge_once(); assert(state.session.has_ip && !root_ready(&state));
        rootless(false); bridge_once(); mqtt_connected();
        assert(state.session.mqtt_connected && got_ip_posts==(different ? 2U : 1U));
    }
}
static void check_lease_guards(void) {
    connected(true); ip_event_got_ip_t e=lease();
    lease_bound=false; got_ip(&e); bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    lease_bound=true; netif_up=false; bridge_once(); assert(!state.session.has_ip);
    netif_up=true; link_up=false; bridge_once(); assert(!state.session.has_ip);
    link_up=true; dhcp=ESP_NETIF_DHCP_STOPPED; bridge_once(); assert(!state.session.has_ip);
    dhcp=ESP_NETIF_DHCP_STARTED; tcpip_error=ESP_FAIL; bridge_once(); assert(!state.session.has_ip);
    tcpip_error=0; ip_error=ESP_FAIL; bridge_once(); assert(!state.session.has_ip);
    ip_error=0; bridge_once(); mqtt_connected(); assert(state.session.has_ip);
    lease_bound=false; bridge_once(); assert(!state.session.has_ip && mqtt_destroys==1);
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
    /* Even a stale SDK-up/BOUND snapshot cannot revive a disconnected parent. */
    lease(); dhcp=ESP_NETIF_DHCP_STARTED; got_ip(&e); bridge_once(); assert(!state.session.has_ip);
    connected(false); lease(); dhcp=ESP_NETIF_DHCP_STARTED;
    got_ip(&e); bridge_once(); assert(!state.session.has_ip && mqtt_starts==1);
}
static void lose_parent_during_read(void) {
    mesh_session_parent_disconnected(&state.session); state.lease_observation_ready=false;
}
static void demote_during_read(void) {
    sdk_root=false; mesh_session_set_role(&state.session,false); state.lease_observation_ready=false;
}
static void rootless_during_read(void) { mesh_session_set_rootless(&state.session,true); }
static void sdk_demote_during_read(void) { sdk_root=false; }
static void reconnect_before_observation(void) { connected(true); }
static void check_lease_races(void) {
    connected(true); lease(); ip_read_hook=lose_parent_during_read;
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    connected(true); lease(); ip_read_hook=demote_during_read;
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    connected(true); lease(); ip_read_hook=sdk_demote_during_read;
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    sdk_root=true; connected(true); lease(); observation_hook=reconnect_before_observation;
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    lease(); ip_read_hook=rootless_during_read;
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts); /* epoch changed during read */
    bridge_once(); assert(state.session.has_ip && !mqtt_starts); /* fresh observation, still rootless */
    rootless(false); bridge_once(); mqtt_connected(); assert(state.session.mqtt_connected);
}
static void inspect_dhcp_transition(void) {
    assert(!state.lease_observation_ready && !state.session.has_ip);
    reconcile_root_lease(NULL); assert(!state.session.has_ip);
}
static void check_dhcp_transition(void) {
    connected(true); ip_event_got_ip_t e=lease(); got_ip(&e); assert(state.session.has_ip);
    dhcp_stop_hook=inspect_dhcp_transition; connected(true);
    bridge_once(); assert(!state.session.has_ip && !mqtt_starts);
    e=lease(); stop_error=ESP_FAIL; connected(true); got_ip(&e); bridge_once();
    assert(!state.session.has_ip && !state.lease_observation_ready);
    stop_error=0; start_error=ESP_FAIL; connected(true); got_ip(&e); bridge_once();
    assert(!state.session.has_ip && !state.lease_observation_ready);
    start_error=0; connected(true); e=lease(); got_ip(&e); bridge_once(); assert(state.session.has_ip);
}
static void check_root_mac_lifetime(void) {
    mesh_transport_status_t s; const uint8_t zero[6]={0};
    mesh_event_root_address_t a={.addr={2,0,0,0,0,30}}, b={.addr={2,0,0,0,0,31}};
    connected(false); event(MESH_EVENT_ROOT_ADDRESS,&a);
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL); connected(false);
    mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,zero,6) && !s.root_change_count);
    event(MESH_EVENT_ROOT_ADDRESS,&b); mesh_transport_get_status(&s);
    assert(!memcmp(s.root_mac,b.addr,6) && s.root_change_count==1);
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL); connected(false); event(MESH_EVENT_ROOT_ADDRESS,&b);
    mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,b.addr,6) && s.root_change_count==1);
    rootless(true); rootless(false); mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,zero,6));
    event(MESH_EVENT_ROOT_ADDRESS,&b); mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,b.addr,6));
    rootless(true); event(MESH_EVENT_ROOT_ADDRESS,&a); rootless(false);
    mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,a.addr,6) && s.root_change_count==2);
    connected(true); event(MESH_EVENT_ROOT_ADDRESS,&a); connected(false);
    mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,zero,6));
    event(MESH_EVENT_ROOT_ADDRESS,&b); event(MESH_EVENT_STOPPED,NULL);
    event(MESH_EVENT_ROOT_ADDRESS,&b); event(MESH_EVENT_STARTED,NULL); connected(false);
    mesh_transport_get_status(&s); assert(!memcmp(s.root_mac,zero,6));
}
static esp_netif_ip_info_t interval_address;
static void interval_step(void) {
    if(delay_count==10) sdk_lost();
    if(delay_count==30) {
        sdk_acquire(interval_address);
        assert(!state.session.has_ip && got_ip_posts==1);
    }
    if(delay_count==120) longjmp(worker_exit,1);
}
static void check_lease_interval(void) {
    connected(true); interval_address=lease().ip_info; sdk_acquire(interval_address);
    unsigned observations=lease_observations;
    delay_hook=interval_step;
    if(!setjmp(worker_exit)) mqtt_bridge(NULL);
    delay_hook=NULL;
    /* 120 real bridge iterations: checks at 1s/2s plus the loss callback.
     * The same lease returns at 1.3s without GOT_IP and is observed at 2s. */
    assert(lease_observations-observations==3);
    assert(state.session.has_ip && mqtt_starts==2 && mqtt_destroys==1 && got_ip_posts==1);
}
#include "recovery_cases.h"
#include "tx_stall_cases.h"
#include "delivery_cases.h"
#include "review_cases.h"
#include "rootmac_cases.h"
#include "ab4_cases.h"
#include "ab5_cases.h"
#include "lr2_cases.h"
#include "ble_scan_cases.h"
#include "v2_cases.h"
int main(int argc,char **argv) {
    assert(argc==2 && mesh_transport_init()==ESP_OK);
    if(!strcmp(argv[1],"sdk_upstream_contract")) {
        mesh_wire_message_t m={.kind=MESH_MESSAGE_NODE_STATUS,.length=2};
        strcpy(m.node_id,own_id);strcpy(m.payload,"{}");
        uint8_t wire[MESH_WIRE_MAX_SIZE];size_t length;
        assert(mesh_wire_encode(&m,wire,sizeof(wire),&length));
        mesh_data_t data={.data=wire,.size=length,.proto=MESH_PROTO_BIN,.tos=MESH_TOS_DEF};
        mesh_addr_t root={{2,0,0,0,0,9}};sdk_root=false;
        assert(esp_mesh_send(NULL,&data,MESH_DATA_TODS,NULL,0)==ESP_ERR_MESH_ARGUMENT);
        assert(esp_mesh_send(NULL,&data,MESH_DATA_P2P|MESH_DATA_NONBLOCK,NULL,0)==ESP_ERR_MESH_ARGUMENT);
        assert(esp_mesh_send(&root,&data,MESH_DATA_P2P,NULL,0)==ESP_ERR_MESH_ARGUMENT);
        assert(esp_mesh_send(&root,&data,MESH_DATA_P2P|MESH_DATA_NONBLOCK,NULL,0)==ESP_ERR_MESH_ARGUMENT);
        data.tos=MESH_TOS_P2P;
        assert(esp_mesh_send(NULL,&data,MESH_DATA_TODS,NULL,0)==ESP_OK);
        assert(esp_mesh_send(NULL,&data,MESH_DATA_TODS|MESH_DATA_NONBLOCK,NULL,0)==ESP_OK);
        assert(esp_mesh_send(&root,&data,MESH_DATA_P2P,NULL,0)==ESP_OK);
        assert(send_calls==3);
        return 0;
    }
    if(!strcmp(argv[1],"policy_error")) policy_error=ESP_ERR_INVALID_STATE;
    if(!strcmp(argv[1],"policy_readback")) wrong_readback=true;
    esp_err_t result=mesh_transport_start();
    if(policy_error || wrong_readback) {
        assert(result==(policy_error ? policy_error : ESP_FAIL));
        assert(!mesh_starts && !config_set && !state.session.started && !mqtt_starts);
        assert(policy_error ? !policy_verified : policy_verified); return 0;
    }
    assert(result==ESP_OK && mesh_starts==1 && policy_verified && !conflicts);
    event(MESH_EVENT_STARTED,NULL);
    if(!strncmp(argv[1],"v2_",3)) { v2_test(argv[1]); return 0; }
    if(!strncmp(argv[1],"scan_",5)) { test_ble_scan_transport(argv[1]);return 0; }
    if(!strncmp(argv[1],"lr2_",4)) { lr2_pacing(argv[1]);return 0; }
    if(!strcmp(argv[1],"ble_roles")) {
        assert(!ble_root);
        sdk_root=true; event(MESH_EVENT_ROOT_SWITCH_ACK,NULL); assert(ble_root);
        event(MESH_EVENT_ROOT_SWITCH_ACK,NULL); assert(ble_root);
        event(MESH_EVENT_STOPPED,NULL); assert(!ble_root); /* SDK flag may be stale. */
        sdk_root=false; event(MESH_EVENT_STARTED,NULL); assert(!ble_root);
        sdk_root=true; event(MESH_EVENT_ROOT_SWITCH_ACK,NULL); assert(ble_root);
        sdk_root=false; event(MESH_EVENT_ROOT_SWITCH_ACK,NULL); assert(!ble_root);
    }
    else if(!strcmp(argv[1],"ble_child_bridge")) {
        connected(true); ip_event_got_ip_t e=lease(); got_ip(&e);
        bridge_once(); mqtt_connected(); assert(ble_root);
        publication_t record={0};
        record.message.kind=MESH_MESSAGE_BLE_RAW;
        strcpy(record.message.node_id,"020000000099");
        const char *payload="{\"protocol_version\":2,\"node_id\":\"020000000099\",\"relay_node_id\":\"020000000099\",\"revision\":0,\"seq\":1,\"boot_id\":\"0123456789abcdef\"}";
        strcpy(record.message.payload,payload);record.message.length=strlen(payload);
        record.queued_ms=now_ms(); record.source[0]=0x02;record.source[5]=0x99;
        transport_state_t view=snapshot();
        assert(mesh_session_accept_ingress(&view.session));
        assert(enqueue_root(&record,&view));bridge_once();
        assert(!strcmp(last_topic,"omk-relay/020000000099/ble/raw"));
        assert(last_enqueue_qos==0 && state.status.mqtt_published==1);
    }
    else if(!strncmp(argv[1],"ab5_",4)) test_ab5(argv[1]);
    else if(!strncmp(argv[1],"ab4_",4)) test_ab4(argv[1]);
    else if(!strncmp(argv[1],"rootmac_",8)) test_rootmac(argv[1]);
    else if(!strncmp(argv[1],"review_",7)) test_review(argv[1]);
    else if(!strcmp(argv[1],"delivery_roundtrip")) test_delivery_roundtrip();
    else if(!strncmp(argv[1],"delivery_",9)) test_delivery(argv[1]);
    else if(!strncmp(argv[1],"receipt_",8)) test_receipt_root(argv[1]);
    else if(!strncmp(argv[1],"tx_",3)) test_tx_stall(argv[1]);
    else if(!strncmp(argv[1],"rec_",4)) test_recovery(argv[1]);
    else if(!strcmp(argv[1],"policy")) {}
    else if(!strcmp(argv[1],"rootless_ip")) check_rootless_ip();
    else if(!strcmp(argv[1],"stale_ip")) check_stale_ip();
    else if(!strcmp(argv[1],"epoch_race")) check_epoch_race();
    else if(!strcmp(argv[1],"role_switch")) check_role_switch();
    else if(!strcmp(argv[1],"diagnostics")) check_diagnostics();
    else if(!strcmp(argv[1],"io_counters")) check_io_counters();
    else if(!strcmp(argv[1],"same_lease")) check_reacquisition(false,false);
    else if(!strcmp(argv[1],"different_lease")) check_reacquisition(true,false);
    else if(!strcmp(argv[1],"rootless_lease")) check_reacquisition(false,true);
    else if(!strcmp(argv[1],"lease_guards")) check_lease_guards();
    else if(!strcmp(argv[1],"lease_races")) check_lease_races();
    else if(!strcmp(argv[1],"dhcp_transition")) check_dhcp_transition();
    else if(!strcmp(argv[1],"root_mac_lifetime")) check_root_mac_lifetime();
    else if(!strcmp(argv[1],"lease_interval")) check_lease_interval();
    else assert(0);
    puts("ok"); return 0;
}
