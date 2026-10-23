#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "idf_stubs.h"

/* Compile actual production paths, exposing static task iterations solely to
 * drive the deterministic scheduler. The sender runs on a real pthread. */
#include "mesh_liveness.c"
#include "mesh_tx_policy.c"
#include "mesh_recovery.c"
#include "mesh_root_recovery.c"
#include "mqtt_registration.c"
#include "sen66_delivery.c"
#include "sen66_diagnostics.c"
#define TAG manager_tag
#include "sensor_manager.c"
#undef TAG
#include "sensor_drivers.c"
#include "mqtt_dispatch.inc"

static atomic_uint_fast64_t clock_ms;
static bool saved_registered = true, task_fail, enqueue_fail, status_fail;
static char saved_id[49] = "sen66-002";
static event_handler_t mqtt_handler, ip_handler;
static _Thread_local bool in_mqtt_callback;
static unsigned starts, reconnects, status_calls, subscriptions, closes;
static bool capacity_model;
static unsigned registration_bytes;
static const char *denied_control_topic;
static bool deny_subscription;
static char will_topic[80], will_payload[128];
static sensor_slot_t slot;
static esp_err_t read_result;
static bool data_ready_value = true;
static struct mock_mqtt_config mock_config;
static struct mock_mqtt_client mock_client = {.config=&mock_config};
#ifdef OMK_NETWORK_DIAGNOSTICS
static pthread_mutex_t diagnostic_api_lock;
static int xSemaphoreTakeRecursive(pthread_mutex_t *lock, unsigned wait) {
    assert(wait==0);return pthread_mutex_trylock(lock)==0;
}
static void xSemaphoreGiveRecursive(pthread_mutex_t *lock) { assert(!pthread_mutex_unlock(lock)); }
static int outbox_get_size(void *outbox) {(void)outbox;return 0;}
/* Exact diagnostic-only helper from the reproducibly patched MQTT source. */
#include "diagnostic_mqtt.inc"
void diag_bridge_fault(void);
void diag_bridge_advance(uint64_t us);
unsigned diag_bridge_saves(void);
#endif
static esp_mqtt_event_t event_queue[2];
static unsigned event_count, event_capacity=CONFIG_MQTT_EVENT_QUEUE_SIZE, dropped;
static bool legacy_notifications, legacy_pending;
static unsigned posts, probe_sends;
static bool probe_no_ack;
static int ordinary_denial;
static omk_mesh_tx_policy_t tx_policy;
static char trace[64][64], published[64][256], published_topics[64][80];
static unsigned trace_count;
static char deliveries[8][3072], topics[8][80];
static unsigned deliveries_count;
static char last_status[256];
static struct { char topic[80], payload[256]; } retained[32];
static unsigned retained_count, nvs_saves, nvs_clears;
static void retain_message(const char *topic, const char *payload) {
    unsigned i;
    for (i=0;i<retained_count;++i) if (!strcmp(retained[i].topic,topic)) break;
    assert(i<32);
    if (i==retained_count) ++retained_count;
    strcpy(retained[i].topic,topic); strcpy(retained[i].payload,payload);
}
static const char *retained_payload(const char *topic) {
    for (unsigned i=0;i<retained_count;++i)
        if (!strcmp(retained[i].topic,topic)) return retained[i].payload;
    return "";
}
static bool change_state_during_status, disconnect_during_status;
static bool update_during_ack, ack_in_progress, defer_service;


static pthread_mutex_t gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gate_changed = PTHREAD_COND_INITIALIZER;
static int block_api;
static bool entered, released;
static void block_at(int api);
static void setup(bool sensor);
static void internal_event(int id, const esp_mqtt_event_t *event);
static void deliver_callback(int id, void *event) {
    assert(!in_mqtt_callback);
    in_mqtt_callback=true;
    mqtt_handler(NULL,MQTT_EVENTS,id,event);
    in_mqtt_callback=false;
}

int64_t esp_timer_get_time(void) { return (int64_t)atomic_load(&clock_ms) * 1000; }
void esp_fill_random(void *b, size_t n) { memset(b, 0x12, n); }
void vTaskDelay(unsigned ms) { atomic_fetch_add(&clock_ms, ms); }
int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg,
                unsigned priority, TaskHandle_t *out) {
    (void)fn; (void)arg;
    assert(priority == 5);
    if (!strcmp(name, "sen66_tx")) assert(stack == 4096);
    if (task_fail) return 0;
    if (out) *out = (void *)1;
    return pdPASS;
}
void xTaskNotifyGive(TaskHandle_t task) { assert(task); }
unsigned ulTaskNotifyTake(int clear, unsigned wait) { (void)clear; (void)wait; return 0; }
esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *c, void **bus) { (void)c; *bus=(void *)1; return ESP_OK; }
esp_err_t i2c_del_master_bus(void *bus) { (void)bus; return ESP_OK; }
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *ap) { ap->rssi=-70; return ESP_OK; }
esp_err_t esp_event_handler_register(esp_event_base_t b, int id, event_handler_t h, void *arg) {
    (void)b; (void)id; (void)arg; ip_handler=h; return ESP_OK;
}
esp_err_t node_identity_get_id(uint64_t *id) { *id=0x9490b46aee0d; return ESP_OK; }
void mesh_network_log_diagnostics(const char *event, uint32_t reason) { (void)event; (void)reason; }
esp_err_t node_registration_get_provisioning_state(bool wifi, uint8_t *state) {
    (void)wifi; *state=saved_registered?OMK_NODE_PROVISIONING_STATE_REGISTERED:1; return ESP_OK;
}
esp_err_t node_registration_get_logical_id(char *id, size_t size) {
    if (!saved_registered) return ESP_ERR_NVS_NOT_FOUND;
    assert(strlen(saved_id)<size); strcpy(id,saved_id); return ESP_OK;
}
esp_err_t node_registration_save(const char *id) { ++nvs_saves; strcpy(saved_id,id); saved_registered=true; return ESP_OK; }
esp_err_t node_registration_clear(void) { ++nvs_clears; saved_registered=false; saved_id[0]=0; return ESP_OK; }

static void note(const char *s) { assert(trace_count<64); strcpy(trace[trace_count++],s); }
esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *c) {
    if (c->session.last_will.topic) {
        strcpy(will_topic,c->session.last_will.topic);
        strcpy(will_payload,c->session.last_will.msg);
        assert(c->session.last_will.qos==0 && c->session.last_will.retain==1);
    }
    return &mock_client;
}
esp_err_t esp_mqtt_client_start(void *c) { assert(c); ++starts; return ESP_OK; }
esp_err_t esp_mqtt_client_reconnect(void *c) { assert(c); ++reconnects; return ESP_OK; }
esp_err_t esp_mqtt_client_register_event(void *c, int id, event_handler_t h, void *arg) {
    assert(c && id==MQTT_EVENT_ANY); (void)arg; mqtt_handler=h; return ESP_OK;
}
int esp_mqtt_client_subscribe_bounded(esp_mqtt_client_handle_t c, const char *topic, int qos, unsigned limit) {
    assert(limit==OMK_MQTT_OUTBOX_BYTES);
    assert(!registration_sender_enabled || !in_mqtt_callback);
    assert(c && !strcmp(topic,registration_config_topic) && qos==1);
    if (deny_subscription) return -3;
    ++subscriptions; note("subscribe"); return 1;
}
int esp_mqtt_client_publish(void *c, const char *topic, const char *payload, int len, int qos, int retain) {
    if(capacity_model && qos==1) {
        unsigned payload_bytes=len>0?(unsigned)len:strlen(payload);
        if(registration_bytes+payload_bytes>OMK_MQTT_OUTBOX_BYTES) return -2;
        registration_bytes+=payload_bytes+strlen(topic)+7;
        return 42;
    }

    assert(!registration_sender_enabled || !in_mqtt_callback);
    assert(c && !len && retain);
    if (!strcmp(topic,registration_topic)) {
        assert(!ack_in_progress);
        block_at(3);
        assert(qos==1); ++status_calls; strcpy(last_status,payload); note("registration_status");
        if (change_state_during_status) {
            change_state_during_status=false;
            mqtt_registration_set_sen66_connected(false);
        }
        if (disconnect_during_status) {
            disconnect_during_status=false;
            internal_event(MQTT_EVENT_ERROR,NULL);
            internal_event(MQTT_EVENT_DISCONNECTED,NULL);
            return -1;
        }
        if (status_fail) return -1;
    } else if (!strcmp(topic,registration_ack_topic)) {
        block_at(4);
        if (update_during_ack) {
            update_during_ack=false; ack_in_progress=true;
            mqtt_registration_set_sen66_connected(false);
            mqtt_registration_set_sen66_diagnostics(7, 2);
            ack_in_progress=false;
        }
        assert(qos==1); note(payload[0]?"ack":"clear_ack");
    } else if (!strcmp(topic,registration_config_topic)) {
        assert(qos==1 && !payload[0]); note("clear_config");
    } else {
        assert(qos==0); note(payload[0]?"online":"clear_online");
    }
    strcpy(published[trace_count-1],payload);
    strcpy(published_topics[trace_count-1],topic);
    retain_message(topic,payload);
    return 1;
}
static void block_at(int api) {
    assert(pthread_mutex_lock(&gate_lock)==0);
    if (api==block_api) {
        entered=true;
        pthread_cond_broadcast(&gate_changed);
        while (!released) pthread_cond_wait(&gate_changed,&gate_lock);
    }
    assert(pthread_mutex_unlock(&gate_lock)==0);
}
int esp_mqtt_client_publish_bounded(esp_mqtt_client_handle_t c,const char *t,const char *p,
                                    int len,int qos,int retain,unsigned limit) {
    assert(limit==OMK_MQTT_OUTBOX_BYTES && (qos==0 || qos==1));
    if (denied_control_topic && !strcmp(t,denied_control_topic)) return -3;
    unsigned required=(len>0?(unsigned)len:strlen(p))+strlen(t)+9;
    if(capacity_model && (registration_bytes>limit || required>limit-registration_bytes)) return -2;
    return esp_mqtt_client_publish(c,t,p,len,qos,retain);
}
int esp_mqtt_client_enqueue(void *c, const char *topic, const char *payload, int len, int qos, int retain, bool store) {
    if (qos == 1) {
        assert(c && len == 1 && !retain && store && !strcmp(payload,"1"));
        assert(!strcmp(topic,"omk-liveness/9490b46aee0d"));
        assert(!in_mqtt_callback);
        ++probe_sends;
        block_at(5);
        esp_mqtt_event_t ack = {.msg_id=42};
        if (!probe_no_ack) deliver_callback(MQTT_EVENT_PUBLISHED,&ack); /* before enqueue returns */
        return 42;
    }
    assert(c && len>=0 && !qos && !retain && store);
#ifdef OMK_NETWORK_DIAGNOSTICS
    /* Model the regular API's recursive acquisition beneath the real helper. */
    assert(!pthread_mutex_lock(&diagnostic_api_lock));
    assert(!pthread_mutex_unlock(&diagnostic_api_lock));
#endif
    block_at(1);
    if (enqueue_fail) return -1;
    assert(deliveries_count<8);
    strcpy(topics[deliveries_count],topic);
    strcpy(deliveries[deliveries_count++],payload);
    return 0;
}
int esp_mqtt_client_get_outbox_size(void *c) { assert(c); block_at(2); return 0; }
#ifndef OMK_NETWORK_DIAGNOSTICS
int esp_mqtt_client_enqueue_bounded(esp_mqtt_client_handle_t c,const char *t,const char *p,int len,int qos,int retain,
                                    unsigned limit,void (*submitted)(int)) {
    (void)limit;
    if(qos==1) {
        ++probe_sends; block_at(5);
        if(submitted) submitted(42);
        esp_mqtt_event_t ack={.msg_id=42};
        if(!probe_no_ack) deliver_callback(MQTT_EVENT_PUBLISHED,&ack);
        return 42;
    }
    if(ordinary_denial) return ordinary_denial;
    int result=esp_mqtt_client_enqueue(c,t,p,len,qos,retain,true);
    if(submitted) submitted(result);
    return result;
}
int esp_mqtt_client_get_outbox_size_nowait(esp_mqtt_client_handle_t c) {return esp_mqtt_client_get_outbox_size(c);}
#endif
static bool accept_ip_event = true;
bool mesh_netif_accept_ip_event(const ip_event_got_ip_t *event, bool acquired) {assert(acquired);return event && accept_ip_event;}
bool mesh_netif_allow_data(void) {return mesh_tx_policy_allow(&tx_policy,(uint32_t)atomic_load(&clock_ms));}

/* esp_event_loop_run(loop, 0) in IDF 6.0.1 executes at most one event.
 * Internal posts from the same running task fail immediately on a full
 * queue; the vendor dispatcher intentionally used above ignores that error. */
esp_err_t esp_event_post_to(void *loop, const char *base, int id, const void *data,
                            size_t size, unsigned wait) {
    (void)loop; (void)base; (void)wait;
    assert(size==sizeof(esp_mqtt_event_t));
    if (event_count==event_capacity) { ++dropped; return ESP_ERR_TIMEOUT; }
    event_queue[event_count]=*(const esp_mqtt_event_t *)data;
    event_queue[event_count++].event_id=id;
    if (id==MQTT_USER_EVENT) ++posts;
    return ESP_OK;
}
esp_err_t esp_event_loop_run(void *loop, unsigned ticks) {
    (void)loop; assert(ticks==0);
    if (!event_count) return ESP_OK;
    esp_mqtt_event_t event=event_queue[0];
    memmove(event_queue,event_queue+1,--event_count*sizeof(event));
    if (event.event_id==MQTT_USER_EVENT && legacy_notifications) legacy_pending=false;
    else deliver_callback(event.event_id,&event);
    return ESP_OK;
}
static void internal_event(int id, const esp_mqtt_event_t *event) {
    mock_client.event=event?*event:(esp_mqtt_event_t){0};
    mock_client.event.event_id=id;
    assert(esp_mqtt_dispatch_event(&mock_client)==ESP_OK);
}
static void legacy_notify(void) {
    if (legacy_pending) return;
    esp_mqtt_event_t event={.client=&mock_client};
    legacy_pending=true;
    assert(esp_mqtt_dispatch_custom_event(&mock_client,&event)==ESP_OK);
}
static void event_collision_test(bool legacy) {
    setup(false); defer_service=true;
    legacy_notifications=legacy;
    /* Reproduce the reviewed capacity-2 failure regardless of the fixed
     * build's capacity, then run production notifications at its real size. */
    if (legacy) event_capacity=2;
    mqtt_registration_set_sen66_connected(true);
    if (legacy) legacy_notify();
    else mqtt_registration_flush_sensor_status();
    char body[]="{\"protocol_version\":1,\"logical_id\":\"after-data\"}";
    esp_mqtt_event_t data={.topic=registration_config_topic,.topic_len=(int)strlen(registration_config_topic),
        .data=body,.data_len=(int)strlen(body),.total_data_len=(int)strlen(body)};
    internal_event(MQTT_EVENT_DATA,&data);
    if (legacy) assert(event_count==1 && event_queue[0].event_id==MQTT_EVENT_DATA);
    else assert(event_count==0 && registration_request_pending);
    mqtt_registration_set_sen66_connected(false);
    if (legacy) legacy_notify();
    else mqtt_registration_flush_sensor_status();
    internal_event(MQTT_EVENT_DISCONNECTED,NULL);
    while (event_count) esp_event_loop_run(NULL,0);
    if (legacy) {
        assert(dropped==1 && mqtt_disconnect_count==0 && client_connected);
        assert(posts==2);
    } else {
        assert(dropped==0 && posts==0 && mqtt_disconnect_count==1 && !client_connected);
        internal_event(MQTT_EVENT_CONNECTED,NULL);
        mqtt_registration_flush_sensor_status();
        assert(strstr(last_status,"after-data") && strstr(last_status,"\"connected_sensors\":[]"));
        assert(sensor_status_version==sensor_status_sent_version);
    }
}

sensor_identity_t sen66_sensor_identify(void *bus) { (void)bus; return SENSOR_ID_EXACT; }
esp_err_t sen66_sensor_probe(void *bus, sen66_sensor_t *s) { (void)bus; s->state=SEN66_SENSOR_STATE_PROBED; return ESP_OK; }
esp_err_t sen66_sensor_start(void *bus, sen66_sensor_t *s) { (void)bus; s->state=SEN66_SENSOR_STATE_MEASURING; return ESP_OK; }
esp_err_t sen66_sensor_read(sen66_sensor_t *s, sen66_measurement_t *m, bool *ready) {
    assert(s->state==SEN66_SENSOR_STATE_MEASURING);
    *ready=data_ready_value;
    *m=(sen66_measurement_t){.co2_ppm=500,.uptime_ms=(uint32_t)atomic_load(&clock_ms)};
    return read_result;
}
void sen66_sensor_close(sen66_sensor_t *s) { s->state=SEN66_SENSOR_STATE_ABSENT; ++closes; }
sensor_identity_t sensor_discover(const sensor_endpoint_t *e, const sensor_driver_t *const *drivers,
                                 size_t count, const sensor_driver_t **selected) {
    (void)e; assert(count==1); *selected=drivers[0]; return SENSOR_ID_EXACT;
}
static void connect_mqtt(void) {
    deliver_callback(MQTT_EVENT_CONNECTED,NULL);
    if (!defer_service) mqtt_registration_flush_sensor_status();
}
static void configure(const char *id) {
    char body[128];
    if (id) snprintf(body,sizeof(body),"{\"protocol_version\":1,\"logical_id\":\"%s\"}",id);
    else strcpy(body,"{\"protocol_version\":1,\"logical_id\":null}");
    esp_mqtt_event_t e={.topic=registration_config_topic,.topic_len=(int)strlen(registration_config_topic),
        .data=body,.data_len=(int)strlen(body),.total_data_len=(int)strlen(body)};
    /* Match Gateway: only removal requests are retained. */
    if (!id) retain_message(registration_config_topic,body);
    deliver_callback(MQTT_EVENT_DATA,&e);
    if (!defer_service) mqtt_registration_flush_sensor_status();
}
static void setup(bool sensor) {
    assert(mqtt_registration_start()==ESP_OK);
    assert(starts==1); connect_mqtt(); trace_count=0;
    if (sensor) {
        slot.endpoint.handle=(void *)1;
        sensor_slot_tick(&slot);
        assert(slot.driver && sen66_connected);
    }
}
static void tick(uint64_t time) { atomic_store(&clock_ms,time); sensor_slot_tick(&slot); }
static void *send_thread(void *unused) { (void)unused; send_latest(); return NULL; }
static void *status_thread(void *unused) { (void)unused; mqtt_registration_flush_sensor_status(); return NULL; }
static pthread_t start_blocked(int api) {
    block_api=api; pthread_t thread;
    assert(pthread_create(&thread,NULL,send_thread,NULL)==0);
    pthread_mutex_lock(&gate_lock);
    while (!entered) pthread_cond_wait(&gate_changed,&gate_lock);
    pthread_mutex_unlock(&gate_lock);
    return thread;
}
static void unblock(pthread_t thread) {
    pthread_mutex_lock(&gate_lock); released=true; pthread_cond_broadcast(&gate_changed);
    pthread_mutex_unlock(&gate_lock);
    assert(pthread_join(thread,NULL)==0); block_api=0;
}
static double field(const char *json, const char *key) {
    cJSON *j=cJSON_Parse(json); assert(j);
    cJSON *v=cJSON_GetObjectItemCaseSensitive(j,key); assert(cJSON_IsNumber(v));
    double d=v->valuedouble; cJSON_Delete(j); return d;
}
static void stall_test(int api) {
    setup(true); tick(10000);
    pthread_t thread=start_blocked(api);
    for (unsigned i=1;i<=120;++i) tick(10000U+i*10000U);
    sen66_diagnostics_t d=sen66_diagnostics_snapshot();
    assert(d.read_attempts==121 && d.read_success==121 && d.read_errors==0);
    assert(d.max_interval_ms==10000 && slot.recovery_count==0 && slot.measurement_timeout_count==0);
    assert(d.enqueue_success==(api==1?0U:1U));
    assert(occupied && pending.sample.sequence==121 && replaced==119);
    char state[512]; assert(sen66_delivery_format(state,sizeof(state)));
    assert(field(state,"phase")== (api==1?SEN66_TX_ENQUEUE:SEN66_TX_OUTBOX));
    assert(field(state,"phase_age_ms")==1200000);
    unblock(thread);
    d=sen66_diagnostics_snapshot(); assert(d.max_publish_ms==1200000);
    assert((api==1?max_enqueue_ms:max_outbox_ms)==1200000);
    assert(field(deliveries[0],"uptime_ms")==10000); // delayed ORIGINAL time
    assert(strstr(deliveries[0],"\"seq\":1,\"enqueue_seq\":1"));
    send_latest();
    assert(deliveries_count==2 && strstr(deliveries[1],"\"seq\":121,\"enqueue_seq\":2"));
    assert(field(deliveries[1],"uptime_ms")==1210000);
}
static void relay_registration_test(void) {
    saved_registered=false; setup(false);
    assert(!sen66_connected && !sen66_diagnostics_snapshot().boot_id);
    trace_count=0; connect_mqtt();
    assert(trace_count==4 && !strcmp(trace[0],"clear_ack") && !strcmp(trace[1],"clear_config"));
    assert(!strcmp(trace[2],"subscribe") && !strcmp(trace[3],"registration_status"));
    trace_count=0; configure("relay-001");
    assert(trace_count==3 && !strcmp(trace[0],"online") && !strcmp(trace[1],"ack") && !strcmp(trace[2],"registration_status"));
    assert(strstr(last_status,"\"connected_sensors\":[]") && strstr(last_status,"relay-001"));
    trace_count=0; deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);
    ip_event_got_ip_t ip={0}; ip_handler(NULL,IP_EVENT,IP_EVENT_STA_GOT_IP,&ip);
    assert(reconnects==1);
    accept_ip_event=false; // the lifecycle module rejected a stale event
    ip_handler(NULL,IP_EVENT,IP_EVENT_STA_GOT_IP,&ip);
    assert(reconnects==1);
    accept_ip_event=true; connect_mqtt();
    assert(trace_count==3 && !strcmp(trace[0],"subscribe") && !strcmp(trace[1],"registration_status") && !strcmp(trace[2],"online"));
    trace_count=0; configure(NULL);
    assert(trace_count==4 && !strcmp(trace[0],"clear_online") && !strcmp(trace[1],"clear_ack") && !strcmp(trace[2],"clear_config") && !strcmp(trace[3],"registration_status"));
    assert(!strstr(last_status,"logical_id") && !saved_registered);
}
static void assert_final_registration(const char *id) {
    assert(!registration_request_pending);
    assert(!registration_request.clear_retained_config);
    assert(!retained_payload(registration_config_topic)[0]);
    cJSON *status=cJSON_Parse(retained_payload(registration_topic)); assert(status);
    cJSON *state=cJSON_GetObjectItemCaseSensitive(status,"registration_state");
    assert(cJSON_IsString(state));
    cJSON *logical=cJSON_GetObjectItemCaseSensitive(status,"logical_id");
    const char *ack=retained_payload(registration_ack_topic);
    if (id) {
        assert(saved_registered && !strcmp(saved_id,id));
        assert(!strcmp(state->valuestring,"registered"));
        assert(cJSON_IsString(logical) && !strcmp(logical->valuestring,id));
        cJSON *j=cJSON_Parse(ack); assert(j);
        logical=cJSON_GetObjectItemCaseSensitive(j,"logical_id");
        assert(cJSON_IsString(logical) && !strcmp(logical->valuestring,id));
        cJSON_Delete(j);
        assert(device_status_configured && !strcmp(device_status_logical_id,id));
        assert(strstr(retained_payload(device_status_topic),"online"));
    } else {
        assert(!saved_registered && !saved_id[0] && !device_status_configured);
        assert(!strcmp(state->valuestring,"provisioned") && !logical && !ack[0]);
        assert(!retained_payload(device_status_topic)[0]);
    }
    cJSON_Delete(status);
    assert(posts==0 && dropped==0);
}
static pthread_t start_blocked_status(int api) {
    block_api=api; pthread_t thread;
    assert(pthread_create(&thread,NULL,status_thread,NULL)==0);
    pthread_mutex_lock(&gate_lock);
    while (!entered) pthread_cond_wait(&gate_changed,&gate_lock);
    pthread_mutex_unlock(&gate_lock);
    return thread;
}
static void burst_request(unsigned i, const char *last) {
    if (i==12) configure(last);
    else if (i==4 || i==10) configure(NULL);
    else {
        char id[24]; snprintf(id,sizeof(id),"superseded-%u",i); configure(id);
        memset(id,'x',strlen(id)); /* MQTT/cJSON/caller storage is not retained. */
    }
}
static void latest_burst_test(const char *mode) {
    const char *last=!strcmp(mode,"clear")?NULL:"latest-id";
    if (!strcmp(mode,"register")) { saved_registered=false; saved_id[0]=0; }
    setup(true); tick(10000); defer_service=true;
    pthread_t thread=start_blocked_status(3);
    for (unsigned i=1;i<=120;++i) {
        tick(10000U+i*10000U);
        if (i<=12) burst_request(i,last);
        if (i==6) deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);
        if (i==7) connect_mqtt();
    }
    assert(nvs_saves==0 && nvs_clears==0);
    assert(registration_request_pending && registration_request_version==12);
    assert(!strcmp(registration_request.logical_id,last?last:""));
    assert(registration_request.clear_retained_config);
    configure("invalid/id"); // Invalid later input cannot replace the valid request.
    assert(registration_request_version==12);
    assert(sen66_diagnostics_snapshot().read_success==121);
    assert(slot.recovery_count==0 && slot.measurement_timeout_count==0);
    unblock(thread);
    assert(registration_request_pending);
    mqtt_registration_flush_sensor_status(); // normal next sender iteration, no re-send
    assert_final_registration(last);
    assert(nvs_saves==(last?1U:0U) && nvs_clears==(last?0U:1U));
    /* Reconnect must not replay a coalesced removal and undo latest-id. */
    deliver_callback(MQTT_EVENT_DISCONNECTED,NULL); connect_mqtt();
    mqtt_registration_flush_sensor_status(); assert_final_registration(last);
    assert(sen66_diagnostics_snapshot().max_interval_ms==10000);
}
static void latest_inflight_test(bool old_clear, bool last_clear) {
    setup(false); defer_service=true; // relay-only Node
    configure(old_clear?NULL:"inflight-id");
    pthread_t thread=start_blocked_status(4); // NVS changed, old ACK/ACK deletion blocked
    for (unsigned i=1;i<=12;++i) burst_request(i,last_clear?NULL:"latest-id");
    uint32_t latest=registration_request_version;
    assert(latest==13 && registration_request_pending);
    deliver_callback(MQTT_EVENT_DISCONNECTED,NULL); connect_mqtt();
    unblock(thread);
    /* Old completion must not clear the new version, payload, pending bit or
     * the obligation to delete a superseded retained removal. */
    assert(registration_request_pending && registration_request_version==latest);
    assert(!strcmp(registration_request.logical_id,last_clear?"":"latest-id"));
    assert(registration_request.clear_retained_config);
    assert(!sen66_diagnostics_snapshot().boot_id);
    mqtt_registration_flush_sensor_status();
    assert_final_registration(last_clear?NULL:"latest-id");
    assert(!retained_payload("omk/sen66-002/status")[0] || !old_clear);
}

static void probe_lifecycle_test(void) {
    setup(false);
    assert(probe_sends==1 && mqtt_registration_get_progress().ack_count==1);
    probe_no_ack=true;
    atomic_store(&clock_ms,30000); mqtt_registration_flush_sensor_status();
    assert(probe_sends==2 && mqtt_registration_get_progress().pending_id==42);
    deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);
    connect_mqtt();
    for (unsigned i=2;i<=40;i++) {
        atomic_store(&clock_ms,i*30000); mqtt_registration_flush_sensor_status();
    }
    assert(probe_sends==2 && !mqtt_registration_get_progress().ack_seen);
    esp_mqtt_event_t other={.msg_id=7};
    deliver_callback(MQTT_EVENT_PUBLISHED,&other);
    assert(!mqtt_registration_get_progress().ack_seen);
    esp_mqtt_event_t expired={.msg_id=42};
    deliver_callback(MQTT_EVENT_DELETED,&expired);
    assert(!mqtt_registration_get_progress().pending_id && mqtt_expired_messages==1);
    probe_no_ack=false;
    mqtt_registration_flush_sensor_status();
    assert(probe_sends==3 && mqtt_registration_get_progress().ack_seen);
    assert(!posts && !dropped); /* Existing MQTT events untouched. */
}
static void probe_stall_test(void) {
    setup(true);
    atomic_store(&clock_ms,30000);
    pthread_t thread=start_blocked_status(5);
    omk_mesh_recovery_t policy;
    omk_mesh_root_recovery_t root_policy;
    mesh_recovery_init(&policy,false,1); policy.jitter_ms=0;
    mesh_root_recovery_init(&root_policy);
    unsigned restart_count=0;
    for (unsigned i=3;i<=32;i++) {
        tick(i*10000);
        omk_mesh_recovery_input_t in={.started=true,.parent_connected=true,.has_ip=true,
            .mqtt_started=true,.mqtt_connected=mqtt_registration_is_connected(),
            .mqtt_progress=mqtt_registration_get_progress().ack_seen};
        omk_mesh_recovery_action_t a=mesh_recovery_choose(&policy,i*10000,&in,&root_policy);
        if(i>=9) assert(policy.stage!=OMK_RECOVERY_HEALTHY);
        if(a==OMK_RECOVERY_RESTART) {
            assert(i==27); ++restart_count;
            mesh_recovery_complete(&policy,i*10000,a,true);
        }
    }
    assert(restart_count==1 && sen66_diagnostics_snapshot().read_success==30);
    assert(mqtt_registration_is_connected() && !mqtt_registration_get_progress().ack_seen);
    unblock(thread);
    assert(mqtt_registration_get_progress().ack_seen);
    assert(!posts && !dropped);
}
static void pressure_or_capacity(bool pressure) {
    setup(true);
    if(!pressure) ordinary_denial=-2;
    for(unsigned i=1;i<=120;i++) {
        tick(i*10000);
        if(pressure) mesh_tx_policy_result(&tx_policy,i*10000,true,false);
        send_latest();
        assert(occupied && pending.sample.sequence==i && !deliveries_count);
        if(i%3==0) service_probe(); // independent low-rate ACKs still work
    }
    assert(sen66_diagnostics_snapshot().read_success==120);
    assert(sen66_diagnostics_snapshot().max_interval_ms==10000);
    assert(mqtt_registration_get_progress().ack_count>=40);
    if(pressure) {
        mesh_tx_policy_result(&tx_policy,1230000,false,true);
        mesh_tx_policy_result(&tx_policy,1230001,false,true);
        mesh_tx_policy_result(&tx_policy,1230002,false,true);
    } else ordinary_denial=0;
    tick(1240000); send_latest();
    assert(deliveries_count==1 && strstr(deliveries[0],"\"seq\":121,"));
    assert(!occupied); // only newest value survives
}
int main(int argc, char **argv) {
    assert(argc==2 || (argc==3 && !strcmp(argv[2],"emit")));
#ifdef OMK_NETWORK_DIAGNOSTICS
    if (!strcmp(argv[1],"relay_diagnostics")) {
        pthread_mutexattr_t attr;assert(!pthread_mutexattr_init(&attr));
        assert(!pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE));
        assert(!pthread_mutex_init(&diagnostic_api_lock,&attr));
        pthread_mutexattr_destroy(&attr);mock_client.api_lock=&diagnostic_api_lock;
        setup(false);assert(sender && registration_sender_enabled && !sen66_connected && !occupied);
        diag_bridge_fault();assert(diag_bridge_saves()==1);
        /* Other thread owns MQTT for twenty simulated minutes. Each sender
         * iteration must return while recording/persistence continues. */
        assert(!pthread_mutex_lock(&diagnostic_api_lock));
        for(unsigned i=1;i<=40;++i) {
            diag_bridge_advance(31000000ULL+i*30000000ULL);
            pthread_t t;assert(!pthread_create(&t,NULL,status_thread,NULL));
            assert(!pthread_join(t,NULL));assert(!deliveries_count);
            send_latest();assert(!occupied);
        }
        assert(diag_bridge_saves()==1);
        assert(!pthread_mutex_unlock(&diagnostic_api_lock));
        diag_bridge_advance(1300000000ULL);mqtt_registration_flush_sensor_status();send_latest();
        assert(deliveries_count==1 && !strcmp(topics[0],"omk/node/9490b46aee0d/network_diagnostics"));
        assert(strstr(deliveries[0],"\"schema\":2") && strstr(deliveries[0],"\"critical\":1"));
        assert(diag_bridge_saves()==1 && !sen66_connected);
        puts(deliveries[0]);return 0;
    }
#endif
    if (!strcmp(argv[1],"registration_retry")) {
        setup(true);defer_service=true;configure("retry-id");
        denied_control_topic=registration_ack_topic;
        for(unsigned i=1;i<=120;i++) {
            tick(i*10000U);mqtt_registration_flush_sensor_status();
            assert(registration_request_pending && nvs_saves==1);
        }
        assert(slot.recovery_count==0 && sen66_diagnostics_snapshot().read_success==120);
        assert(trace_count==1 && !strcmp(trace[0],"online")); // successful steps not duplicated
        send_latest();assert(deliveries_count==1); // measurement delivery can still run
        denied_control_topic=NULL;mqtt_registration_flush_sensor_status();
        assert(!registration_request_pending && nvs_saves==1);
        assert(trace_count==3 && !strcmp(trace[1],"ack") && !strcmp(trace[2],"registration_status"));
    } else if (!strcmp(argv[1],"subscription_retry")) {
        setup(false);unsigned before=subscriptions;
        deny_subscription=true;defer_service=true;
        deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);connect_mqtt();
        for(unsigned i=0;i<120;i++) mqtt_registration_flush_sensor_status();
        assert(subscriptions==before && registration_connection_handled!=registration_connection_version);
        deny_subscription=false;status_fail=true;mqtt_registration_flush_sensor_status();
        assert(subscriptions==before+1 && registration_connection_handled!=registration_connection_version);
        status_fail=false;mqtt_registration_flush_sensor_status();
        assert(subscriptions==before+1 && registration_connection_handled==registration_connection_version);
        esp_mqtt_event_t e={.msg_id=1234};deliver_callback(MQTT_EVENT_DELETED,&e);
        mqtt_registration_flush_sensor_status();assert(subscriptions==before+2); // expired control retried
        assert(nvs_saves==0 && nvs_clears==0);
    } else if (!strcmp(argv[1],"clear_retry_superseded")) {
        setup(false);defer_service=true;configure(NULL);
        denied_control_topic=registration_config_topic;mqtt_registration_flush_sensor_status();
        assert(nvs_clears==1 && registration_request_pending && registration_apply_step==3);
        for(unsigned i=0;i<10;i++) mqtt_registration_flush_sensor_status();
        assert(nvs_clears==1);
        configure("latest-id");mqtt_registration_flush_sensor_status();
        assert(nvs_saves==1 && registration_request_pending);
        denied_control_topic=NULL;mqtt_registration_flush_sensor_status();
        assert(!registration_request_pending && nvs_saves==1 && nvs_clears==1);
        assert(!strcmp(saved_id,"latest-id") && strstr(last_status,"latest-id"));
        assert(!retained_payload(registration_config_topic)[0]);
    } else if (!strcmp(argv[1],"registration_capacity")) {
        setup(false);capacity_model=true;
        registration_bytes=OMK_MQTT_OUTBOX_BYTES;
        clear_retained_registration_ack();
        assert(registration_bytes<=OMK_MQTT_OUTBOX_BYTES);
        clear_retained_registration_config();
        assert(registration_bytes<=OMK_MQTT_OUTBOX_BYTES);
    }
    else if (!strcmp(argv[1],"pressure")) pressure_or_capacity(true);
    else if (!strcmp(argv[1],"capacity")) pressure_or_capacity(false);
    else if (!strcmp(argv[1],"probe_lifecycle")) probe_lifecycle_test();
    else if (!strcmp(argv[1],"probe_stall")) probe_stall_test();
    else if (!strncmp(argv[1],"latest_burst_",13)) latest_burst_test(argv[1]+13);
    else if (!strcmp(argv[1],"inflight_register_register")) latest_inflight_test(false,false);
    else if (!strcmp(argv[1],"inflight_register_clear")) latest_inflight_test(false,true);
    else if (!strcmp(argv[1],"inflight_clear_register")) latest_inflight_test(true,false);
    else if (!strcmp(argv[1],"inflight_clear_clear")) latest_inflight_test(true,true);
    else if (!strcmp(argv[1],"legacy_event_collision")) event_collision_test(true);
    else if (!strcmp(argv[1],"fixed_event_collision")) event_collision_test(false);
    else if (!strcmp(argv[1],"enqueue_20min")) stall_test(1);
    else if (!strcmp(argv[1],"outbox_20min")) stall_test(2);
    else if (!strcmp(argv[1],"relay_registration")) relay_registration_test();
    else if (!strcmp(argv[1],"startup_failure")) {
        task_fail=true; setup(true); assert(!sender);
        for (unsigned i=1;i<=120;++i) tick(i*10000U);
        assert(sen66_diagnostics_snapshot().read_success==120 && unavailable==120 && deliveries_count==0);
        assert(slot.recovery_count==0);
        trace_count=0; configure("sen66-new"); assert(!strcmp(trace[1],"ack"));
        trace_count=0; configure(NULL); assert(!strcmp(trace[3],"registration_status"));
    } else if (!strcmp(argv[1],"replace_expire")) {
        setup(true); tick(10000); tick(20000);
        assert(replaced==1); ((sen66_context_t *)slot.context)->sample.co2_ppm=999;
        send_latest(); assert(field(deliveries[0],"co2_ppm")==500);
        tick(30000); atomic_store(&clock_ms,60001); send_latest(); assert(expired==1 && deliveries_count==1);
        tick(70000); deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);
        tick(80000); send_latest(); assert(deliveries_count==1 && occupied);
        trace_count=0; connect_mqtt(); send_latest(); assert(field(deliveries[1],"uptime_ms")==80000);
        assert(strstr(will_topic,"sen66-002/status") && strstr(will_payload,"offline"));
    } else if (!strcmp(argv[1],"registration_inflight")) {
        setup(true); tick(10000); pthread_t thread=start_blocked(1);
        tick(20000); defer_service=true; configure("sen66-new");
        assert(registration_request_pending && !strcmp(saved_id,"sen66-002"));
        unblock(thread); mqtt_registration_flush_sensor_status();
        assert(!occupied); tick(30000); send_latest();
        assert(!strcmp(topics[0],"omk/sen66-002/sen66") && field(deliveries[0],"uptime_ms")==10000);
        assert(!strcmp(topics[1],"omk/sen66-new/sen66"));
        tick(40000); configure(NULL); mqtt_registration_flush_sensor_status();
        send_latest(); assert(deliveries_count==2 && !occupied);
    } else if (!strcmp(argv[1],"sensor_recovery")) {
        setup(true); tick(10000); pthread_t thread=start_blocked(1);
        tick(20000); read_result=ESP_ERR_INVALID_STATE; tick(30000);
        assert(slot.recovery_count==1 && !slot.driver && !occupied && closes==1);
        assert(!sen66_connected && sen66_recovery_count==1);
        read_result=ESP_OK; tick(90000); tick(100000);
        assert(sen66_connected && pending.sample.sequence==3);
        unblock(thread); send_latest(); assert(deliveries_count==2);
        data_ready_value=false; for (unsigned i=1;i<=6;++i) tick(100000+i*10000);
        assert(slot.measurement_timeout_count==1); // genuine not-ready still recovers
    } else if (!strcmp(argv[1],"status_coalesce")) {
        setup(true);
        unsigned before=status_calls;
        for (unsigned i=0;i<10;++i) mqtt_registration_set_sen66_diagnostics(i,0);
        assert(status_calls==before);
        mqtt_registration_flush_sensor_status();
        assert(posts==0 && status_calls==before+1);
        configure("sen66-new"); assert(strstr(last_status,"sen66-new"));
        configure(NULL); assert(!strstr(last_status,"logical_id"));
        mqtt_registration_set_sen66_diagnostics(3,0);
        change_state_during_status=true; mqtt_registration_flush_sensor_status();
        assert(sensor_status_version!=sensor_status_sent_version);
        mqtt_registration_flush_sensor_status();
        assert(strstr(last_status,"\"connected_sensors\":[]"));
        assert(sensor_status_version==sensor_status_sent_version);
    } else if (!strcmp(argv[1],"status_failure")) {
        setup(true); status_fail=true; mqtt_registration_flush_sensor_status();
        assert(sensor_status_version!=sensor_status_sent_version);
        status_fail=false; mqtt_registration_flush_sensor_status();
        assert(sensor_status_version==sensor_status_sent_version && posts==0);
    } else if (!strcmp(argv[1],"status_disconnect")) {
        setup(true); disconnect_during_status=true;
        mqtt_registration_flush_sensor_status();
        assert(!client_connected && mqtt_disconnect_count==1 && mqtt_error_events==1);
        assert(dropped==0 && posts==0 && sensor_status_version!=sensor_status_sent_version);
        unsigned before=status_calls;
        mqtt_registration_set_sen66_connected(false);
        mqtt_registration_set_sen66_diagnostics(3,1);
        mqtt_registration_flush_sensor_status(); assert(status_calls==before);
        trace_count=0; connect_mqtt();
        assert(strstr(last_status,"\"connected_sensors\":[]"));
        assert(field(last_status,"sen66_rc")==3 && field(last_status,"sen66_to")==1);
        assert(sensor_status_version==sensor_status_sent_version);
        assert(trace_count==3 && !strcmp(trace[0],"subscribe") && !strcmp(trace[1],"registration_status") && !strcmp(trace[2],"online"));
    } else if (!strcmp(argv[1],"enqueue_failure")) {
        setup(true); tick(10000); enqueue_fail=true; send_latest();
        assert(sen66_diagnostics_snapshot().enqueue_failures==1 && !occupied);
        enqueue_fail=false; tick(20000); send_latest();
        assert(strstr(deliveries[0],"\"seq\":2,\"enqueue_seq\":1"));
        assert(slot.recovery_count==0);
    } else if (!strcmp(argv[1],"status_20min")) {
        setup(true); tick(10000);
        block_api=3; pthread_t thread;
        assert(pthread_create(&thread,NULL,status_thread,NULL)==0);
        pthread_mutex_lock(&gate_lock);
        while (!entered) pthread_cond_wait(&gate_changed,&gate_lock);
        pthread_mutex_unlock(&gate_lock);
        for (unsigned i=1;i<=120;++i) tick(10000U+i*10000U);
        assert(sen66_diagnostics_snapshot().read_success==121 && slot.recovery_count==0);
        char state[512]; assert(sen66_delivery_format(state,sizeof(state)));
        assert(field(state,"status_age_ms")==1200000);
        unblock(thread); assert(max_status_ms==1200000);
        send_latest(); assert(strstr(deliveries[0],"\"seq\":121,\"enqueue_seq\":1"));
    } else if (!strcmp(argv[1],"uptime_wrap")) {
        setup(true);
        const uint64_t wrap=UINT64_C(1)<<32;
        atomic_store(&clock_ms,wrap+1000);
        sen66_measurement_t sample={.uptime_ms=UINT32_MAX-999,.sequence=1};
        assert(sen66_delivery_submit(saved_id,&sample)==ESP_OK); send_latest();
        assert(deliveries_count==1 && field(deliveries[0],"uptime_ms")==UINT32_MAX-999);
        sample.uptime_ms=(uint32_t)(wrap-30001);
        assert(sen66_delivery_submit(saved_id,&sample)==ESP_OK); send_latest();
        assert(expired==1 && deliveries_count==1);
    } else if (!strcmp(argv[1],"payload_capacity")) {
        setup(true);
        memset(&counters,0xff,sizeof(counters));
        submitted=replaced=invalidated=expired=unavailable=UINT32_MAX;
        max_enqueue_ms=max_outbox_ms=max_status_ms=UINT64_MAX;
        char original[1152];
        memset(original,' ',sizeof(original)); memcpy(original,"{\"node_id\":0",12);
        original[1150]='}'; original[1151]=0;
        assert(mqtt_registration_publish_mesh_status(original)==ESP_OK);
        cJSON *j=cJSON_Parse(deliveries[0]); assert(j);
        assert(cJSON_IsObject(cJSON_GetObjectItemCaseSensitive(j,"sen66")));
        cJSON *tx=cJSON_GetObjectItemCaseSensitive(j,"sen66_tx"); assert(cJSON_IsObject(tx));
        assert(cJSON_GetObjectItemCaseSensitive(tx,"max_status_ms"));
        cJSON_Delete(j);
    } else if (!strcmp(argv[1],"status_during_ack")) {
        setup(true); update_during_ack=true; configure("sen66-new");
        assert(trace_count==3 && !strcmp(trace[0],"online") && !strcmp(trace[1],"ack") && !strcmp(trace[2],"registration_status"));
        assert(strstr(last_status,"\"connected_sensors\":[]"));
        assert(field(last_status,"sen66_rc")==7);
        assert(sensor_status_version==sensor_status_sent_version && posts==0);
    } else if (!strcmp(argv[1],"status_registration_overlap")) {
        setup(true); tick(10000); block_api=3; pthread_t thread;
        assert(pthread_create(&thread,NULL,status_thread,NULL)==0);
        pthread_mutex_lock(&gate_lock);
        while (!entered) pthread_cond_wait(&gate_changed,&gate_lock);
        pthread_mutex_unlock(&gate_lock);
        defer_service=true;
        for (unsigned i=0;i<20;++i) {
            mqtt_registration_set_sen66_connected(i%2==0);
            mqtt_registration_set_sen66_diagnostics(i,2);
        }
        configure("first"); configure(NULL); configure("final");
        deliver_callback(MQTT_EVENT_DISCONNECTED,NULL);
        connect_mqtt();
        assert(registration_request_pending && !strcmp(saved_id,"sen66-002"));
        unblock(thread);
        mqtt_registration_flush_sensor_status();
        assert(!strcmp(saved_id,"final") && strstr(last_status,"final"));
        assert(strstr(last_status,"\"connected_sensors\":[]"));
        assert(field(last_status,"sen66_rc")==19);
        assert(sensor_status_version==sensor_status_sent_version && posts==0);
        /* Superseded clear is not applied to NVS, but its retained tombstone
         * and the old availability still have to be removed before final ACK. */
        assert(!strcmp(trace[0],"registration_status"));
        assert(!strcmp(trace[1],"clear_online") && !strcmp(trace[2],"clear_config"));
        assert(!strcmp(trace[3],"online") && !strcmp(trace[4],"ack"));
        assert(!strcmp(trace[5],"registration_status"));
        assert(strstr(published[0],"sen66-002"));
        assert(strstr(published[4],"final") && strstr(published[5],"final"));
        assert(nvs_saves==1 && nvs_clears==0);
        assert(!retained_payload(registration_config_topic)[0]);
    } else assert(false);
    if (argc==3) {
        for (unsigned i=0;i<trace_count;++i)
            if (strcmp(trace[i],"subscribe")) printf("%s\t%s\n",published_topics[i],published[i]);
    }
    if (slot.context) { slot.driver->close(slot.context); free(slot.context); }
    return 0;
}
