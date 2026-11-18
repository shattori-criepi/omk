#include <assert.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <setjmp.h>
#include "node_messages.c"

static char saved_id[49] = "sen66-001";
static uint32_t saved_revision = 7;
static int publish_count, apply_count, drop_count, task_count, notifications;
static int transport_error, snapshot_error, apply_error;
static int identity_error;
static esp_reset_reason_t fake_reset_reason = ESP_RST_TASK_WDT;
static char packets[8][MESH_TRANSPORT_PAYLOAD_MAX + 1];
static int kinds[8];
static char ordering[64];
static unsigned order_length;
static size_t allocations, fail_at, live_allocations;
static pthread_mutex_t apply_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t apply_condition = PTHREAD_COND_INITIALIZER;
static bool block_apply, applying, release_apply;
static int64_t fake_time = 123000000;
static bool cadence, cadence_dirty;
static jmp_buf cadence_done;
static unsigned status_times[8], report_times[16], status_n, report_n;
static mesh_transport_status_t mesh_status = {
    .started = true, .parent_connected = true, .layer = 3,
    .parent_mac = {2,0,0,0,0,1}, .root_mac = {2,0,0,0,0,2}, .self_ap_mac = {2,0,0,0,0,3},
};

const char *esp_err_to_name(int e) { (void)e; return "stub"; }
uint32_t esp_random(void) { return 0x1234abcd; }
int64_t esp_timer_get_time(void) { return fake_time; }
esp_reset_reason_t esp_reset_reason(void) { return fake_reset_reason; }
void mesh_transport_get_status(mesh_transport_status_t *out) { *out = mesh_status; }
int node_identity_get_id_hex(char *out, size_t size) { snprintf(out, size, "020000000041"); return identity_error; }
int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned priority, TaskHandle_t *handle) {
    (void)fn; (void)name; (void)stack; (void)arg; (void)priority;
    ++task_count; *handle = (void *)1; return pdPASS;
}
unsigned ulTaskNotifyTake(int clear, unsigned timeout) {
    (void)clear;
    if(cadence) {
        fake_time+=(int64_t)timeout*1000;
        if(fake_time>=71000000)longjmp(cadence_done,1);
        if(cadence_dirty)ab_runtime_command("{}",2,mesh_status.root_mac);
    }
    return 0;
}
void xTaskNotifyGive(TaskHandle_t handle) { (void)handle; ++notifications; }
uint32_t node_registration_get_revision(void) { return saved_revision; }
int node_registration_snapshot(char *id, size_t size, uint32_t *revision) {
    if (snapshot_error) return snapshot_error;
    snprintf(id, size, "%s", saved_id); *revision = saved_revision; return ESP_OK;
}
int node_registration_apply(uint32_t revision, const char *id) {
    ++apply_count;
    pthread_mutex_lock(&apply_mutex);
    if (block_apply) {
        applying = true; pthread_cond_signal(&apply_condition);
        while (!release_apply) pthread_cond_wait(&apply_condition, &apply_mutex);
    }
    pthread_mutex_unlock(&apply_mutex);
    if (apply_error) return apply_error;
    if (revision < saved_revision || (revision == saved_revision && strcmp(id ? id : "", saved_id))) return ESP_ERR_INVALID_STATE;
    saved_revision = revision; snprintf(saved_id, sizeof(saved_id), "%s", id ? id : ""); return ESP_OK;
}
void mesh_transport_drop_pending(void) { ++drop_count; ordering[order_length++] = 'D'; }
int mesh_transport_publish(mesh_message_kind_t kind, const char *payload, size_t length) {
    assert(length <= MESH_TRANSPORT_PAYLOAD_MAX && length == strlen(payload));
    if(cadence) {
        if(kind==MESH_MESSAGE_NODE_STATUS) {assert(status_n<8);status_times[status_n++]=fake_time/1000;}
        if(kind==MESH_MESSAGE_MEASUREMENT) {assert(report_n<16);report_times[report_n++]=fake_time/1000;}
        return ESP_OK;
    }
    assert(publish_count < 8);
    strcpy(packets[publish_count], payload); kinds[publish_count++] = kind;
    ordering[order_length++] = 'P';
    return transport_error;
}
static void reset_output(void) { publish_count = 0; order_length = 0; memset(ordering, 0, sizeof(ordering)); }
static cJSON *last(void) { assert(publish_count); cJSON *p = cJSON_Parse(packets[publish_count - 1]); assert(p); return p; }
static void number(cJSON *p, const char *key, double expected) { cJSON *v=cJSON_GetObjectItemCaseSensitive(p,key); assert(cJSON_IsNumber(v) && v->valuedouble==expected); }
static void string(cJSON *p, const char *key, const char *expected) { cJSON *v=cJSON_GetObjectItemCaseSensitive(p,key); assert(cJSON_IsString(v) && !strcmp(v->valuestring,expected)); }
static sen66_measurement_t sample(void) { return (sen66_measurement_t){.pm1_0_ug_m3=NAN,.pm2_5_ug_m3=2,.pm4_0_ug_m3=3,.pm10_0_ug_m3=4,.relative_humidity_percent=50,.temperature_celsius=23.5,.voc_index=100,.nox_index=INFINITY,.co2_ppm=600,.uptime_ms=1234,.sequence=42}; }
static const uint8_t md[] = {2,0,0,0,0,4,0xf4,3,4,0x99,0x2c}, sd[] = {0x54,0,100};
static int ble(void) { return node_messages_publish_ble_relay("020000000041","020000000004",-42,md,sizeof(md),sd,sizeof(sd)); }
static void command(const char *s) { node_messages_handle_config(s, strlen(s)); }
static void *allocate(size_t size) { if (allocations++ >= fail_at) return NULL; void *p=malloc(size); if(p) ++live_allocations; return p; }
static void release(void *p) { if(p) { assert(live_allocations); --live_allocations; free(p); } }
static void validate_record(const char *payload, int kind) {
    cJSON *p=cJSON_Parse(payload); assert(p);
    number(p,"protocol_version",2); string(p,"node_id","020000000041");
    assert(cJSON_GetObjectItemCaseSensitive(p,"boot_id")); assert(cJSON_GetObjectItemCaseSensitive(p,"revision"));
    if(kind==MESH_MESSAGE_SEN66) {
        assert(cJSON_GetArraySize(p)==17); /* five common + device/uptime/meta + nine measurements */
        cJSON *meta=cJSON_GetObjectItemCaseSensitive(p,"meta"); assert(cJSON_GetArraySize(meta)==3);
        assert(cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(p,"nox_index")));
    } else if(kind==MESH_MESSAGE_BLE_RAW) {
        assert(cJSON_GetArraySize(p)==10);
        cJSON *a=cJSON_GetObjectItemCaseSensitive(p,"manufacturer_data"); assert(cJSON_GetArraySize(a)==1);
        assert(cJSON_GetArraySize(cJSON_GetArrayItem(a,0))==2);
        a=cJSON_GetObjectItemCaseSensitive(p,"service_data"); assert(cJSON_GetArraySize(a)==1);
        assert(cJSON_GetArraySize(cJSON_GetArrayItem(a,0))==2);
    } else {
        assert(cJSON_GetArraySize(p)==(kind==MESH_MESSAGE_NODE_STATUS ? 32 : 12));
        cJSON *a=cJSON_GetObjectItemCaseSensitive(p,"connected_sensors"); assert(cJSON_GetArraySize(a)==1);
    }
    cJSON_Delete(p);
}
static void allocation_failures(void) {
    node_messages_set_sen66_connected(true);
    for (int kind=0;kind<3;kind++) for (size_t n=0;n<240;n++) {
        reset_output(); allocations=0; fail_at=n; live_allocations=0;
        cJSON_Hooks hooks={allocate,release}; cJSON_InitHooks(&hooks);
        sen66_measurement_t s=sample();
        if(kind==0) node_messages_publish_sen66(saved_id,&s);
        else if(kind==1) ble(); else send_state();
        assert(live_allocations==0);
        cJSON_InitHooks(NULL);
        for(int i=0;i<publish_count;i++) validate_record(packets[i],kinds[i]);
    }
}
static void config_cases(void) {
    command("{\"protocol_version\":2,\"revision\":8,\"logical_id\":\"new-name\"}");
    assert(drop_count==1 && ordering[0]=='D' && ordering[1]=='P');
    cJSON *p=last(); number(p,"revision",8); string(p,"logical_id","new-name"); string(p,"result","applied"); cJSON_Delete(p);
    reset_output(); command("{\"protocol_version\":2,\"revision\":8,\"logical_id\":\"new-name\"}");
    assert(drop_count==1); p=last(); string(p,"result","applied"); cJSON_Delete(p);
    reset_output(); command("{\"protocol_version\":2,\"revision\":7,\"logical_id\":\"old-name\"}");
    p=last(); number(p,"revision",8); string(p,"logical_id","new-name"); string(p,"result","rejected"); cJSON_Delete(p);
    apply_error=ESP_FAIL; reset_output(); command("{\"protocol_version\":2,\"revision\":9,\"logical_id\":null}");
    p=last(); number(p,"revision",8); string(p,"result","rejected"); cJSON_Delete(p); assert(drop_count==1);
    apply_error=0; reset_output(); command("{\"protocol_version\":2,\"revision\":9,\"logical_id\":null}  \n");
    p=last(); number(p,"revision",9); string(p,"registration_state","provisioned"); assert(cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(p,"logical_id"))); cJSON_Delete(p); assert(drop_count==2);
}
static void *apply_thread(void *arg) { (void)arg; command("{\"protocol_version\":2,\"revision\":8,\"logical_id\":\"new-name\"}"); return NULL; }
static void nonblocking(void) {
    block_apply=true; pthread_t thread; assert(!pthread_create(&thread,NULL,apply_thread,NULL));
    pthread_mutex_lock(&apply_mutex); while(!applying) pthread_cond_wait(&apply_condition,&apply_mutex); pthread_mutex_unlock(&apply_mutex);
    sen66_measurement_t s=sample(); assert(node_messages_publish_sen66("sen66-001",&s)==ESP_ERR_INVALID_STATE); assert(ble()==ESP_ERR_INVALID_STATE); assert(!publish_count);
    pthread_mutex_lock(&apply_mutex); release_apply=true; pthread_cond_signal(&apply_condition); pthread_mutex_unlock(&apply_mutex); pthread_join(thread,NULL);
    assert(node_messages_publish_sen66("sen66-001",&s)==ESP_ERR_INVALID_STATE);
    assert(node_messages_publish_sen66("new-name",&s)==ESP_OK);
    cJSON *p=last(); number(p,"revision",8); string(p,"device_id","new-name"); cJSON_Delete(p);
}
static bool sensor_ready=true; static int sensor_error;
int sen66_sensor_probe(i2c_master_bus_handle_t bus,sen66_sensor_t *sensor) { (void)bus;(void)sensor;return ESP_OK; }
int sen66_sensor_start(i2c_master_bus_handle_t bus,sen66_sensor_t *sensor) { (void)bus;(void)sensor;return ESP_OK; }
int sen66_sensor_read(sen66_sensor_t *sensor,sen66_measurement_t *out,bool *is_ready) { (void)sensor; *out=sample(); *is_ready=sensor_ready; return sensor_error; }
void sen66_sensor_close(sen66_sensor_t *sensor) { (void)sensor; }
sensor_identity_t sen66_sensor_identify(i2c_master_bus_handle_t bus) { (void)bus;return SENSOR_ID_EXACT; }
static void driver(void) {
    assert(sensor_driver_count==1); const sensor_driver_t *d=sensor_drivers[0]; void *ctx=calloc(1,d->context_size); assert(ctx); bool r;
    sensor_endpoint_t endpoint={.location.i2c_address=0x6b}; assert(d->accepts(&endpoint)); assert(d->start(&endpoint,ctx)==ESP_OK);
    assert(d->read(ctx,&r)==ESP_OK && r); assert(d->publish(ctx,"sen66-001")==ESP_OK); cJSON *p=last(); number(p,"seq",1); cJSON_Delete(p);
    sensor_ready=false; assert(d->read(ctx,&r)==ESP_OK && !r); sensor_error=ESP_FAIL; sensor_ready=true; assert(d->read(ctx,&r)==ESP_FAIL);
    sensor_error=0; assert(d->read(ctx,&r)==ESP_OK && r); assert(d->publish(ctx,"sen66-001")==ESP_OK); p=last(); number(p,"seq",2); cJSON_Delete(p);
    d->connected(true); d->diagnostics(4,2); reset_output(); send_state(); p=last(); number(p,"sen66_rc",4); number(p,"sen66_to",2); cJSON_Delete(p); d->close(ctx); free(ctx);
}
int main(int argc,char **argv) {
    assert(argc==2); sen66_measurement_t s=sample(); command("{\"protocol_version\":2,\"revision\":8,\"logical_id\":null}"); assert(!apply_count && !publish_count);
    assert(node_messages_publish_sen66(saved_id,&s)==ESP_ERR_INVALID_STATE);
    if(!strcmp(argv[1],"start")) { identity_error=ESP_FAIL; assert(node_messages_start()==ESP_FAIL); assert(!identity[0]); identity_error=0; }
    if(!strncmp(argv[1],"diagnostic_limits",17) || !strncmp(argv[1],"registration_limits",19))
        fake_reset_reason=ESP_RST_CPU_LOCKUP;
    assert(node_messages_start()==ESP_OK && node_messages_start()==ESP_OK && task_count==1);
    if(!strcmp(argv[1],"schema")) { node_messages_set_sen66_connected(true); node_messages_set_sen66_diagnostics(12,3); send_state(); assert(node_messages_publish_sen66(saved_id,&s)==ESP_OK); assert(ble()==ESP_OK); for(int i=0;i<publish_count;i++) puts(packets[i]); }
    else if(!strncmp(argv[1],"cadence_",8)) {
        cadence=true;cadence_dirty=!strcmp(argv[1],"cadence_dirty");fake_time=1000000;
        if(!setjmp(cadence_done))state_task(NULL);
        assert(status_n==3 && status_times[0]==1000 && status_times[1]==31000 && status_times[2]==61000);
        if(cadence_dirty) {
            assert(report_n==7);
            for(unsigned i=0;i<report_n;i++)assert(report_times[i]==(i+1)*10000);
        } else assert(!report_n);
    }
    else if(!strcmp(argv[1],"allocations")) allocation_failures();
    else if(!strcmp(argv[1],"config")) config_cases();
    else if(!strcmp(argv[1],"review_depth")) {
        assert(CJSON_NESTING_LIMIT==8);
        char payload[208];strcpy(payload,"{\"x\":");size_t n=strlen(payload);
        for(int i=0;i<100;++i)payload[n++]='[';
        payload[n++]='0';for(int i=0;i<100;++i)payload[n++]=']';payload[n++]='}';payload[n]=0;
        assert(n==207);command(payload);assert(!apply_count && !publish_count);
        config_cases(); /* ordinary config, idempotent repeat and stale revision still work */
    }
    else if(!strcmp(argv[1],"nonblocking")) nonblocking();
    else if(!strcmp(argv[1],"driver")) driver();
    else if(!strcmp(argv[1],"start")) { assert(strlen(boot_id)==16); assert(ble()==ESP_OK); }
    else if(!strncmp(argv[1],"diagnostic_limits",17) || !strncmp(argv[1],"registration_limits",19)) {
        memset(saved_id, 'z', sizeof(saved_id)-1); saved_id[sizeof(saved_id)-1]=0;
        saved_revision=UINT32_MAX; status_seq=UINT32_MAX-1; fake_time=INT64_MAX;
        node_messages_set_sen66_connected(true);
        node_messages_set_sen66_diagnostics(UINT32_MAX,UINT32_MAX);
        mesh_status.started=mesh_status.parent_connected=false; mesh_status.layer=INT_MIN;
        memset(mesh_status.parent_mac,255,6); memset(mesh_status.root_mac,255,6); memset(mesh_status.self_ap_mac,255,6);
        mesh_status.parent_disconnect_count=mesh_status.root_change_count=UINT32_MAX;
        mesh_status.tx_accepted=mesh_status.tx_dropped=mesh_status.rx_dropped=UINT32_MAX;
        mesh_status.mesh_tx_ok=mesh_status.mesh_tx_errors=mesh_status.mesh_rx_ok=mesh_status.mesh_rx_errors=UINT32_MAX;
        format_errors=send_errors=UINT16_MAX;
        mesh_status.recovery_action=UINT8_MAX;
        mesh_status.recovery_requests=mesh_status.recovery_ok=mesh_status.recovery_link=UINT16_MAX;
        mesh_status.mqtt_pubacks=mesh_status.recovery_votes=mesh_status.recovery_demotions=UINT16_MAX;
        mesh_status.recovery_error=INT32_MIN;
        mesh_status.last_disconnect_valid=true; mesh_status.last_disconnect_reason=255; mesh_status.last_disconnect_rssi=INT8_MIN;
        if(!strcmp(argv[1],"diagnostic_limits_null")) mesh_status.last_disconnect_valid=false;
        if(!strncmp(argv[1],"registration_limits",19)) {
            bool ack=!strcmp(argv[1],"registration_limits_ack");
            publish(ack ? MESH_MESSAGE_REGISTRATION_ACK : MESH_MESSAGE_REGISTRATION_STATUS,
                    state_record(ack ? "rejected" : NULL));
            assert(publish_count==1);
        } else {
            send_state(); assert(publish_count==2);
        }
        for(int i=0;i<publish_count;i++) puts(packets[i]);
    }
    else if(!strcmp(argv[1],"invalid_registration")) {
        for(unsigned i=0;i<3;++i) {
            reset_output(); snapshot_error=i==0 ? ESP_FAIL : 0;
            memset(saved_id,i==1 ? '"' : 1,48); saved_id[48]=0;
            send_state(); assert(publish_count==1 && kinds[0]==MESH_MESSAGE_NODE_STATUS);
            cJSON *p=last(); string(p,"registration_state","provisioned");
            assert(cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(p,"logical_id")));
            assert(cJSON_GetObjectItemCaseSensitive(p,"reg_err")->valueint!=0);
            number(p,"revision",0); assert(strlen(packets[0])<920); cJSON_Delete(p); puts(packets[0]);
        }
    }
    else if(!strcmp(argv[1],"message_errors")) {
        assert(publish(MESH_MESSAGE_NODE_STATUS,NULL)==ESP_ERR_NO_MEM);
        assert(format_errors==1 && send_errors==0);
        cJSON *huge=record(1,7); char text[1025];memset(text,'a',1024);text[1024]=0;
        cJSON_AddStringToObject(huge,"huge",text);
        assert(publish(MESH_MESSAGE_NODE_STATUS,huge)==ESP_ERR_INVALID_SIZE);
        assert(format_errors==2 && send_errors==0 && !publish_count);
        transport_error=ESP_ERR_NO_MEM;send_state();assert(format_errors==2 && send_errors==2);
        transport_error=0;reset_output();send_state();
        cJSON *p=cJSON_Parse(packets[0]);number(p,"fmt_err",2);number(p,"send_err",2);cJSON_Delete(p);
        format_errors=send_errors=UINT16_MAX;assert(publish(MESH_MESSAGE_NODE_STATUS,NULL)!=ESP_OK);
        transport_error=ESP_FAIL;send_state();assert(format_errors==UINT16_MAX && send_errors==UINT16_MAX);
    }
    else if(!strcmp(argv[1],"diagnostic_unknown")) {
        memset(&mesh_status,0,sizeof(mesh_status)); mesh_status.layer=-1;
        send_state(); assert(publish_count==2);
        for(int i=0;i<publish_count;i++) puts(packets[i]);
    }
    else if(!strcmp(argv[1],"invalid")) {
        const char *invalid[]={"[]","null","{}","{\"protocol_version\":2,\"revision\":0,\"logical_id\":null}","{\"protocol_version\":2,\"revision\":1.5,\"logical_id\":null}","{\"protocol_version\":2,\"revision\":4294967296,\"logical_id\":null}","{\"protocol_version\":2,\"revision\":true,\"logical_id\":null}","{\"protocol_version\":2,\"revision\":8,\"logical_id\":\"bad/id\"}","{\"protocol_version\":2,\"revision\":8,\"logical_id\":\"\"}","{\"protocol_version\":2,\"revision\":8,\"logical_id\":[],\"other\":1}","{\"protocol_version\":2,\"revision\":8,\"logical_id\":null,\"revision\":9}","{\"protocol_version\":2,\"revision\":8,\"logical_id\":null}garbage"};
        for(size_t i=0;i<sizeof(invalid)/sizeof(*invalid);i++) command(invalid[i]); assert(!apply_count && !publish_count);
    } else if(!strcmp(argv[1],"limits")) {
        uint8_t bytes[31]={0}; assert(node_messages_publish_ble_relay(identity,"bad",-1,bytes,1,NULL,0)==ESP_ERR_INVALID_ARG);
        assert(node_messages_publish_ble_relay(identity,"020000000004",-128,bytes,1,NULL,0)==ESP_ERR_INVALID_ARG);
        assert(node_messages_publish_ble_relay(identity,"020000000004",-1,bytes,32,NULL,0)==ESP_ERR_INVALID_ARG);
        assert(node_messages_publish_ble_relay(identity,"020000000004",-1,NULL,1,NULL,0)==ESP_ERR_INVALID_ARG);
        assert(node_messages_publish_ble_relay(identity,"020000000004",-1,bytes,31,bytes,31)==ESP_OK); assert(strlen(packets[0])<768);
        reset_output(); cJSON *huge=record(1,7); char text[MESH_TRANSPORT_PAYLOAD_MAX+1]; memset(text,'a',sizeof(text)-1); text[sizeof(text)-1]=0; cJSON_AddStringToObject(huge,"oversized",text); assert(publish(MESH_MESSAGE_SEN66,huge)==ESP_ERR_INVALID_SIZE && !publish_count);
        transport_error=ESP_FAIL; assert(node_messages_publish_sen66(saved_id,&s)==ESP_FAIL); transport_error=0; assert(node_messages_publish_sen66(saved_id,&s)==ESP_OK); cJSON *p=last(); number(cJSON_GetObjectItemCaseSensitive(p,"meta"),"enqueue_seq",1); cJSON_Delete(p);
    } else assert(0);
    return 0;
}

void discovery_ble_control_tick(void) {}
