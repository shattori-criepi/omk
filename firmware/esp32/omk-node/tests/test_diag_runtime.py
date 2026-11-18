"""Actual USB-owner scheduler + core, with SDK/Flash boundary doubles only."""
from pathlib import Path
import subprocess
import pytest

BASE = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module",params=[0,1],ids=['legacy','v2-probe'])
def runtime(tmp_path_factory,request):
    d = tmp_path_factory.mktemp("diag-runtime")
    (d / "freertos").mkdir()
    (d / "stubs.h").write_text(r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <assert.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_MESH_TIMEOUT 0x400a
#define ESP_ERR_MESH_NO_MEMORY 0x4007
#define ESP_ERR_MESH_QUEUE_FULL 0x400d
#define ESP_ERR_MESH_XMIT 0x4017
#define ESP_ERR_MESH_DISCONNECTED 0x400b
#define ESP_ERR_MESH_NO_ROUTE_FOUND 0x400f
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
extern int held;
#define portENTER_CRITICAL(p) do { (void)(p); assert(!held); held=1; } while(0)
#define portEXIT_CRITICAL(p) do { (void)(p); assert(held); held=0; } while(0)
int64_t esp_timer_get_time(void);
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_8BIT 2
#define MALLOC_CAP_DMA 4
size_t heap_caps_get_free_size(uint32_t);
size_t heap_caps_get_minimum_free_size(uint32_t);
size_t heap_caps_get_largest_free_block(uint32_t);
typedef struct { int to_parent,to_parent_p2p,to_child,to_child_p2p,mgmt,broadcast; } mesh_tx_pending_t;
typedef struct { int toDS,toSelf; } mesh_rx_pending_t;
esp_err_t esp_mesh_get_tx_pending(mesh_tx_pending_t*);
esp_err_t esp_mesh_get_rx_pending(mesh_rx_pending_t*);
enum {MESH_EVENT_STARTED,MESH_EVENT_STOPPED,MESH_EVENT_PARENT_CONNECTED,
MESH_EVENT_PARENT_DISCONNECTED,MESH_EVENT_LAYER_CHANGE,MESH_EVENT_ROOT_ADDRESS,
MESH_EVENT_NETWORK_STATE,MESH_EVENT_TODS_STATE,MESH_EVENT_VOTE_STARTED,
MESH_EVENT_VOTE_STOPPED,MESH_EVENT_ROOT_SWITCH_REQ,MESH_EVENT_ROOT_SWITCH_ACK};
''')
    for f in ('esp_err.h','esp_mesh.h','esp_heap_caps.h','esp_timer.h','freertos/FreeRTOS.h'):
        (d/f).write_text('#include "stubs.h"\n')
    (d/'sdkconfig.h').write_text('#define CONFIG_IDF_TARGET_ESP32S3 1\n#define CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM 32\n#define CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER 1\n')
    (d/'esp_idf_version.h').write_text('#define ESP_IDF_VERSION_VAL(a,b,c) (((a)<<16)|((b)<<8)|(c))\n#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(6,0,1)\n')
    (d / 'runtime.c').write_text(r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define OMK_DIAGNOSTICS 1
#include "mesh_diag.c"
int held;
static uint64_t now;
static unsigned writes, init_calls, getter_calls, arms;
static bool fail_write, fail_arm;
static bool inject_error;

static diag_store_status_t storage;
static mesh_transport_status_t transport;
static diag_core_t saved[2];
_Alignas(4) unsigned char g_eb_list_desc[220],g_lmac_cnt[192];
void ab_runtime_probe_context(uint8_t *preset,uint32_t *change) { assert(!held); *preset=0; *change=0; }
int64_t esp_timer_get_time(void) { return now; }
void mesh_transport_get_status(mesh_transport_status_t *s) { assert(!held); *s=transport; }
size_t heap_caps_get_free_size(uint32_t c) { assert(!held && (c==3 || c==7)); return 50000; }
size_t heap_caps_get_minimum_free_size(uint32_t c) { assert(!held && c==3); return 40000; }
size_t heap_caps_get_largest_free_block(uint32_t c) { assert(!held && (c==3 || c==7)); return 30000; }
esp_err_t esp_mesh_get_tx_pending(mesh_tx_pending_t *p) { assert(!held); ++getter_calls; if(inject_error) { inject_error=false; mesh_wire_message_t m={.kind=MESH_MESSAGE_SEN66}; mesh_diag_tx_begin(&m,true); mesh_diag_tx_end(0x400a); } p->to_parent_p2p=7; return 0; }
esp_err_t esp_mesh_get_rx_pending(mesh_rx_pending_t *p) { assert(!held); ++getter_calls; p->toSelf=2; return 0; }
void ab_runtime_diagnostic(uint32_t f,uint32_t m,uint32_t a,uint8_t masks,uint8_t phase,uint8_t error) { (void)f;(void)m;(void)a;(void)masks;(void)phase;(void)error;assert(!held); }
uint32_t diag_store_crc32(const void *p,size_t n) { (void)p; return (uint32_t)n; }
void diag_store_init(void) { assert(!held); ++init_calls; storage.ready=!storage.occupied_mask; }
uint32_t diag_store_block_reason(void) {return storage.occupied_mask && !save_attempts ? 1 : 0;}
uint32_t diag_store_phase(void) { return fail_write ? 6 : 0; }
diag_store_status_t diag_store_status(void) { assert(!held); return storage; }
esp_err_t diag_store_save(unsigned slot,const void *p,size_t n) {
    assert(!held && slot<2 && n==sizeof(diag_core_t)); assert(!(storage.attempt_mask&(1u<<slot)));
    saved[slot]=*(const diag_core_t*)p;
    ++writes; storage.attempt_mask|=1u<<slot; storage.occupied_mask|=1u<<slot;
    storage.ready=storage.attempt_mask!=3; storage.last_io_us=123;
    if(fail_write) return -1;
    storage.valid_mask|=1u<<slot; return 0;
}
esp_err_t diag_store_replace(unsigned slot,const void *p,size_t n) {
    assert(storage.valid_mask==3 && !fail_write);
    storage.attempt_mask &= ~(1u<<slot); storage.valid_mask &= ~(1u<<slot);
    storage.ready=1; return diag_store_save(slot,p,n);
}
esp_err_t diag_store_arm(void) { assert(!held); ++arms; if(fail_arm)return -1; memset(&storage,0,sizeof storage); storage.ready=1; return 0; }
static void setup(void) {
    memset(&live,0,sizeof live); memset(&capture,0,sizeof capture);
    memset(&storage,0,sizeof storage); memset(&transport,0,sizeof transport);
    initialized=store_initialized=false; last_poll_ms=last_sample_ms=first_save_ms=0;
    pinned_slot=-1;fault_interval=false;healthy_since_ms=0;save_attempts=capture_token=0; memset(slot_ms,0,sizeof slot_ms); memset(slot_priority,0,sizeof slot_priority); save_failed=false; now=1000000; writes=init_calls=getter_calls=arms=0;
    fail_write=fail_arm=false; held=0;
    g_eb_list_desc[28]=1;g_eb_list_desc[36]=32;
    transport.started=true; transport.parent_connected=true; transport.layer=2;
    memcpy(transport.parent_mac,"parent",6); memcpy(transport.root_mac,"root00",6);
    mesh_diag_boot("020000000001","0123456789abcdef",1);
}
static mesh_wire_message_t message(void) {
    mesh_wire_message_t m={.kind=MESH_MESSAGE_SEN66};
    strcpy(m.node_id,"020000000001");
    strcpy(m.payload,"{\"boot_id\":\"0123456789abcdef\",\"seq\":7}"); m.length=strlen(m.payload); return m;
}
static void send(int error) { mesh_wire_message_t m=message(); unsigned old=getter_calls,io=writes; mesh_diag_tx_begin(&m,true); now+=500; mesh_diag_tx_end(error); assert(getter_calls==old && writes==io); }
int main(int argc,char**argv) {
    assert(argc==2); setup();
    if(!strcmp(argv[1],"normal")) {
        for(unsigned i=0;i<600;++i) { now=1000000ull+i*1000000ull; send(0); mesh_diag_receipt("020000000001","0123456789abcdef",i); mesh_diag_poll(); }
        assert(writes==0 && init_calls==1 && getter_calls<=122);
    } else if(!strcmp(argv[1],"sustained")) {
        for(unsigned i=0;i<700;++i) { now=1000000ull+i*1000000ull; send(0x400a); mesh_diag_poll(); }
        assert(writes==2 && saved[0].first_error_valid && saved[1].first_error.ms==saved[0].first_error.ms);
        assert(saved[1].capture_ms-saved[0].capture_ms>=180000 && saved[1].context.topology.layer==2);
    } else if(!strcmp(argv[1],"inflight")) {
        mesh_wire_message_t m=message(); mesh_diag_tx_begin(&m,true);
        now+=181000000; mesh_diag_poll(); assert(writes==1 && saved[0].inflight);
    } else if(!strcmp(argv[1],"repair_save") || !strcmp(argv[1],"repair_protected")) {
        bool protected=!strcmp(argv[1],"repair_protected");
        if(protected) storage.occupied_mask=storage.valid_mask=3;
        mesh_diag_poll();mesh_diag_recovery(7,0,false,0);now+=1000000;mesh_diag_poll();
        assert(writes==(protected?0:1));
        for(unsigned i=0;i<800;++i) { now+=1000000;mesh_diag_receipt("020000000001","0123456789abcdef",i);mesh_diag_poll(); }
        assert(writes==(protected?0:1));
        if(!protected) assert(saved[0].capture_reason==DIAG_CAPTURE_RECOVERY && saved[0].schema==3);
    } else if(!strcmp(argv[1],"failed_save")) {
        fail_write=true; mesh_diag_recovery(2,0,false,0);
        for(unsigned i=0;i<800;++i) { now+=1000000; mesh_diag_receipt("020000000001","0123456789abcdef",i); mesh_diag_poll(); }
        assert(writes==1 && save_attempts==1);
    } else if(!strcmp(argv[1],"failure_followup")) {
        fail_write=true; mesh_diag_recovery(2,0,false,0); mesh_diag_poll();
        assert(writes==1 && live.save_failure_slot_plus1==1 && live.save_failure_phase==6);
        assert(live.save_failure_error==-1 && !saved[0].save_failure_slot_plus1);
        /* The fixed tuple survives arbitrary event-ring overwrite. */
        for(unsigned i=0;i<40;i++) mesh_diag_recovery(2,0,true,0);
        fail_write=false; now+=240000000; mesh_diag_quality(true); mesh_diag_demand(false,false,0,0,false); mesh_diag_poll();
        assert(writes==2 && saved[1].save_failure_slot_plus1==1);
        assert(saved[1].save_failure_phase==6 && saved[1].save_failure_error==-1);
        assert(live.save_failure_slot_plus1==1);
        assert(mesh_diag_rearm()==0 && !live.save_failure_slot_plus1);
    } else if(!strcmp(argv[1],"reboot_old")) {
        storage.occupied_mask=1; storage.valid_mask=1; mesh_diag_recovery(2,0,false,0);
        for(unsigned i=0;i<800;++i) { now+=1000000; mesh_diag_receipt("020000000001","0123456789abcdef",i); mesh_diag_poll(); }
        assert(writes==0 && init_calls==1);
    } else if(!strcmp(argv[1],"rearm")) {
        mesh_diag_recovery(2,0,false,0); mesh_diag_poll(); assert(writes==1);
        assert(mesh_diag_rearm()==0 && !live.recovery_trigger && live.observation_start_ms==now/1000);
        for(unsigned i=0;i<500;++i) { now+=1000000; send(0); mesh_diag_receipt("020000000001","0123456789abcdef",i); mesh_diag_poll(); }
        assert(writes==1 && arms==1 && storage.ready);
        fail_arm=true; assert(mesh_diag_rearm()!=0);
    } else if(!strcmp(argv[1],"live_token")) {
        size_t n; uint32_t token=mesh_diag_live_begin(&n); uint8_t b[48];
        assert(n==sizeof live && mesh_diag_live_read(token,0,b,sizeof b)==0);
        assert(mesh_diag_live_read(token,(uint32_t)n,b,1)!=0);
        assert(mesh_diag_live_read(token,UINT32_MAX,b,1)!=0);
        mesh_diag_live_begin(&n); assert(mesh_diag_live_read(token,0,b,1)!=0);
        token=mesh_diag_live_begin(&n); mesh_diag_recovery(2,0,false,0); mesh_diag_poll();
        assert(mesh_diag_live_read(token,0,b,1)!=0);
    } else if(!strcmp(argv[1],"root")) {
        transport.is_root=true; transport.layer=1; mesh_diag_mesh_event(MESH_EVENT_LAYER_CHANGE);
        mesh_wire_message_t m=message(); mesh_diag_root(&m,DIAG_ROOT_RX);
        now+=181000000; mesh_diag_poll(); assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_PEER_SILENT);
    } else if(!strcmp(argv[1],"receipt_recovery")) {
        mesh_diag_recovery(2,2,false,0); mesh_diag_poll();
        mesh_diag_recovery(2,2,true,0); send(0);
        mesh_diag_receipt("020000000001","0123456789abcdef",7);
        now+=241000000; mesh_diag_poll();
        assert(writes==1 && live.counts[0]==1 && live.receipt_count==1);
        assert(live.context.mqtt_pubacks==0); /* Root admission != MQTT PUBACK. */
    } else if(!strcmp(argv[1],"rearm_old_recovery") || !strcmp(argv[1],"repair_rearm")) {
        mesh_diag_poll(); now=1000000000; assert(mesh_diag_rearm()==0);
        assert(live.arm_generation==1);
        mesh_diag_recovery(!strcmp(argv[1],"repair_rearm")?7:2,0,false,0); /* old demand */
        assert(!live.recovery_trigger);
        for(unsigned i=0;i<200;i++) { now+=1000000; send(0); mesh_diag_demand(false,false,1,0,false); mesh_diag_poll(); }
        assert(writes==0 && storage.ready);
        for(unsigned i=0;i<181;i++) { now+=1000000;send(0x400a);mesh_diag_poll(); }
        assert(writes==2 && saved[0].arm_generation==1);
        assert(saved[0].first_error.ms>saved[0].observation_start_ms);
        now+=241000000;mesh_diag_poll();assert(writes==2);
    } else if(!strcmp(argv[1],"rearm_inflight")) {
        mesh_diag_poll(); mesh_wire_message_t m=message();mesh_diag_tx_begin(&m,true);
        now+=200000000; assert(mesh_diag_rearm()==0);
        assert(live.inflight && live.inflight_before_arm && live.active_identity.seq==7);
        mesh_diag_poll();assert(writes==0);
        now+=29000000;mesh_diag_poll();assert(writes==0);
        now+=1000000;mesh_diag_poll();assert(writes==1 && saved[0].inflight);
        mesh_diag_tx_end(0x400a);assert(live.counts[1]==1 && !live.inflight);
    } else if(!strcmp(argv[1],"scan_event")) {
        mesh_diag_poll();diag_core_t before=live;
        mesh_diag_scan(now/1000,123,0x301);
        assert(live.event_count==before.event_count+1);
        assert(!live.recovery_trigger && !live.inflight && !live.first_error_valid);
        assert(!memcmp(live.counts,before.counts,sizeof(live.counts)));
        assert(!memcmp(live.request_count,before.request_count,sizeof(live.request_count)));
        mesh_diag_poll();assert(!writes);
    } else if(!strcmp(argv[1],"traffic")) {
        mesh_diag_request(1);mesh_diag_request(2);mesh_diag_request(2);mesh_diag_request(8);
        send(0x400a);send(0);
        mesh_diag_ble(0,false,false,false);mesh_diag_ble(1,true,false,false);
        mesh_diag_ble(2,false,true,false);mesh_diag_ble(3,false,false,true);
        assert(live.request_count[0]==1 && live.request_count[1]==2);
        assert(live.sdk_count[0]==2 && live.sdk_errors[0]==1);
        assert(live.ble_periodic[0]==1 && live.ble_event[1]==1 && live.ble_rejected[2]==1 && live.ble_suppressed[3]==1);
        live.request_count[0]=UINT32_MAX;mesh_diag_request(1);assert(live.request_count[0]==UINT32_MAX);
    } else if(!strcmp(argv[1],"v2_capture") || !strcmp(argv[1],"v2_dump")) {
#if OMK_MESH_PROBE
        mesh_probe_core_t v;
        mesh_probe_begin(1,1); g_eb_list_desc[34]=32;g_lmac_cnt[22]=1;now+=5000000;
        mesh_probe_end(0x4007,1); mesh_probe_copy(&v);
        assert(v.bytes==1424 && v.events[3].raw.used==0 && v.events[4].raw.used==32);
        assert(v.events[4].raw.rejected==1 && v.events[4].reserved[2]==30000 && (v.seen&6)==6);
        mesh_probe_begin(2,1); now+=5000000;mesh_probe_end(0x400a,2);mesh_probe_copy(&v);
        assert(v.events[2].raw.flags&PROBE_MIXED);
        for(unsigned i=0;i<10;i++) { now+=1000000;mesh_probe_poll(); }
        mesh_probe_copy(&v);assert(v.ring_count==8 && v.ring_overwrites==2);
        mesh_probe_recovery(PROBE_BEGIN,0,3);g_eb_list_desc[34]=0;
        mesh_probe_recovery(PROBE_DISCONNECTED,0,3);mesh_probe_copy(&v);
        assert(v.events[8].raw.used==32 && v.events[10].raw.used==0);
        size_t n;uint32_t token=mesh_diag_v2_begin(&n);assert(n==1424);
        assert(mesh_diag_v2_read(token,0,&v,n)==0 && v.events[4].raw.used==32);
        assert(mesh_diag_live_read(token,0,&v,1)!=0);
        assert(mesh_diag_v2_read(token,1424,&v,1)!=0);
        mesh_diag_live_begin(&n);assert(mesh_diag_v2_read(token,0,&v,1)!=0);
        g_eb_list_desc[36]=31;mesh_probe_poll();now+=1000000;mesh_probe_poll();mesh_probe_copy(&v);
        assert(!(v.abi_flags&PROBE_VALID));assert(!writes && !arms);
        if(!strcmp(argv[1],"v2_dump")) assert(fwrite(&v,1,sizeof(v),stdout)==sizeof(v));
#endif
    } else if(!strcmp(argv[1],"early_durable") || !strcmp(argv[1],"flash_dump")) {
        mesh_diag_poll(); now+=1000000;
        mesh_wire_message_t m=message(); mesh_probe_begin(1,12); mesh_diag_tx_begin(&m,true);
        g_eb_list_desc[34]=32; g_lmac_cnt[22]=9; now+=5000000;
        mesh_probe_end(0x4007,12); mesh_diag_tx_end(0x4007); mesh_diag_poll();
        assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_ONSET);
        assert(saved[0].current.ms==now/1000 && saved[0].onset.valid);
        assert(saved[0].first_error.code==0x4007 && saved[0].first_error.identity.seq==7);
#if OMK_MESH_PROBE
        assert(saved[0].probe.events[4].raw.rejected==9 && saved[0].probe.events[4].raw.used==32);
        assert(saved[0].probe.recent[saved[0].probe.ring_head? saved[0].probe.ring_head-1:7].us==now);
#endif
        if(!strcmp(argv[1],"flash_dump")) assert(fwrite(&saved[0],1,sizeof(saved[0]),stdout)==sizeof(saved[0]));
    } else if(!strcmp(argv[1],"noise_then_fault")) {
        mesh_diag_poll(); send(0x400a); now+=1000000; mesh_diag_poll();
        for(unsigned i=0;i<600;i++) { now+=1000000; send(0); mesh_diag_receipt("020000000001","0123456789abcdef",i); mesh_diag_poll(); }
        assert(writes==1 && storage.attempt_mask==1);
        mesh_wire_message_t m=message(); mesh_diag_tx_begin(&m,true);
        now+=30000000; mesh_diag_poll();
        assert(writes==2 && saved[1].capture_reason==DIAG_CAPTURE_TX_STALL && saved[1].inflight);
        now+=900000000; mesh_diag_poll(); assert(writes==3 && saved[0].capture_sequence==3);
        now+=1000000; mesh_diag_poll(); assert(writes==3);
        diag_core_t onset=saved[1];
        now+=900000000; mesh_diag_poll();
        assert(writes==4 && pinned_slot==1 && !memcmp(&onset,&saved[1],sizeof onset));
    } else if(!strcmp(argv[1],"p1_receipt_reconnect")) {
        mesh_diag_poll();
        for(unsigned i=1;i<=1800;i++) {
            now+=1000000;
            if(i%30==0) { transport.parent_connected=false;mesh_diag_mesh_event(MESH_EVENT_PARENT_DISCONNECTED); }
            if(i%30==1) { transport.parent_connected=true;mesh_diag_mesh_event(MESH_EVENT_PARENT_CONNECTED); }
            send(0); /* Deliberately no demand/quality observer updates. */
            mesh_diag_poll();
            if(i==299) assert(writes==0);
            if(i==300) assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_RECEIPT_SILENT);
        }
        assert(saved[0].capture_ms>=301000 && saved[0].capture_ms<303000);
        assert(pinned_slot==0 && writes==2);
    } else if(!strcmp(argv[1],"p1_pin_hours")) {
        mesh_diag_poll();mesh_wire_message_t m=message();mesh_diag_tx_begin(&m,true);
        now+=30000000;mesh_diag_poll();assert(pinned_slot==0 && writes==1);
        diag_core_t initial=saved[0];
        for(unsigned i=0;i<16;i++) { now+=900000000;mesh_diag_poll();assert(!memcmp(&initial,&saved[0],sizeof initial)); }
        assert(writes==17 && saved[1].capture_sequence==17);
        mesh_diag_tx_end(0);mesh_diag_receipt("020000000001","0123456789abcdef",1);
        now+=61000000;mesh_diag_poll();now+=61000000;mesh_diag_poll();assert(pinned_slot==-1);
        mesh_diag_tx_begin(&m,true);now+=30000000;mesh_diag_poll();assert(pinned_slot==0 && saved[0].capture_sequence==18);
        assert(mesh_diag_rearm()==0 && pinned_slot==-1 && !fault_interval);
    } else if(!strcmp(argv[1],"p1_kind8")) {
        mesh_diag_poll();mesh_wire_message_t m=message();m.kind=MESH_MESSAGE_MEASUREMENT;
        mesh_diag_tx_begin(&m,true);now+=30000000;mesh_diag_poll();
        assert(writes==1 && saved[0].inflight && saved[0].active_identity.kind==8);
        assert(saved[0].capture_reason==DIAG_CAPTURE_TX_STALL);
        mesh_diag_tx_end(0x400a);
        assert(!live.inflight && live.last_duration_us==30000000 && live.last_result==0x400a);
        assert(!live.upstream_attempts && !live.first_error_valid && !live.kind_error_mask);
        for(unsigned i=0;i<8;i++) assert(!live.counts[i]);
        for(unsigned i=0;i<7;i++) assert(!live.sdk_count[i] && !live.sdk_errors[i]);
    } else if(!strcmp(argv[1],"sample_race")) {
        inject_error=true; mesh_diag_poll();
        assert(!writes && live.first_error_valid && !live.onset_sample_needed);
        now+=1000000; mesh_diag_poll();
        assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_ONSET && saved[0].onset.valid);
    } else if(!strcmp(argv[1],"parent_fault")) {
        mesh_diag_poll(); transport.parent_connected=false; transport.layer=-1;
        mesh_diag_disconnected((const uint8_t*)"parent",201,-87);
        mesh_diag_mesh_event(MESH_EVENT_PARENT_DISCONNECTED);
        now+=30000000; mesh_diag_poll();
        assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_PARENT_STALL);
        assert(saved[0].disconnect_reason==201 && saved[0].disconnect_rssi==-87);
        assert(!memcmp(saved[0].disconnected_parent,"parent",6));
    } else if(!strcmp(argv[1],"quality_without_action")) {
        mesh_diag_poll(); mesh_diag_quality(true); mesh_diag_demand(true,true,now/1000,0,false);
        now+=1000000; mesh_diag_poll();
        assert(writes==1 && saved[0].capture_reason==DIAG_CAPTURE_QUALITY && !saved[0].recovery_trigger);
    } else if(!strcmp(argv[1],"delivery_without_action")) {
        for(unsigned i=0;i<181;i++) { now+=1000000; send(0); mesh_diag_demand(true,true,0,999999,true); mesh_diag_poll(); }
        assert(writes==1 && saved[0].counts[0]>0 && saved[0].capture_reason==DIAG_CAPTURE_DELIVERY_STALL);
    } else if(!strcmp(argv[1],"slow_success")) {
        mesh_diag_poll(); mesh_wire_message_t m=message(); mesh_diag_tx_begin(&m,true);
        now+=31000000; mesh_diag_tx_end(0); mesh_diag_poll();
        assert(writes==1 && !saved[0].inflight && saved[0].last_result==0);
        assert(saved[0].last_duration_us==31000000);
    } else return 2;
    return 0;
}
''')
    exe=d/'runtime'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all',
                    '-DOMK_DIAGNOSTICS=1','-DOMK_MESH_PROBE='+str(request.param),
                    '-I',str(d),'-I',str(BASE/'src'),str(d/'runtime.c'),str(BASE/'src/diag_core.c'),str(BASE/'src/mesh_probe.c'),'-o',str(exe)],check=True)
    return exe

@pytest.mark.parametrize('case',['p1_receipt_reconnect','p1_pin_hours','p1_kind8','sample_race','early_durable','noise_then_fault','parent_fault','quality_without_action','delivery_without_action','slow_success','repair_rearm','repair_save','repair_protected','v2_capture','failure_followup','normal','sustained','inflight','failed_save','reboot_old','rearm','live_token','root','receipt_recovery','rearm_old_recovery','rearm_inflight','traffic','scan_event'])
def test_runtime(runtime,case):
    subprocess.run([str(runtime),case],check=True,timeout=10)

def test_real_c_payload_decoder(runtime):
    import importlib.util
    data=subprocess.check_output([str(runtime),'v2_dump'])
    if not data:pytest.skip('private probe disabled in legacy fixture')
    spec=importlib.util.spec_from_file_location('probe_v2',BASE/'tools/ble_scan_ab/probe_v2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    r=module.decode(data)
    assert len(data)==1424 and r['node_id']=='020000000001'
    assert r['events']['first_nomem_post']['used']==32
    assert r['events']['first_nomem_post']['largest_internal_dma_8bit']==30000
    assert r['events']['recovery_disconnected']['used']==0
    assert r['events']['first_timeout_post']['flags']&4
    assert r['subtree_routes']==3 and len(r['recent'])==8


def test_flash_payload_includes_real_probe(runtime):
    import importlib.util
    data=subprocess.check_output([str(runtime),'flash_dump'])
    spec=importlib.util.spec_from_file_location('durable_diag',BASE/'tools/legacy_diag/diag_payload.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.decode_payload(data)
    assert len(data)==4056 and result['schema']==3
    assert result['first_error']['identity']['seq']==7
    if result['probe'] is not None:
        assert result['probe']['events']['first_nomem_post']['cap_rejected_u16']==9
        assert result['probe']['events']['first_nomem_post']['used']==32
