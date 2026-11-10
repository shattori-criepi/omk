"""Exercise real C BLE scheduler with deterministic advertisements and backpressure."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]


def test_native_ble_state_and_periodic_delivery(tmp_path):
    (tmp_path / "esp_err.h").write_text('''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM -2
#define ESP_ERR_INVALID_STATE -3
''')
    (tmp_path / "esp_timer.h").write_text('#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n')
    (tmp_path / "esp_log.h").write_text('#pragma once\n#define ESP_LOGW(tag, ...) do { (void)(tag); } while (0)\n')
    (tmp_path / "test.c").write_text(r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "switchbot_relay.c"
static int64_t clock_us;
static unsigned attempts,accepted;
static esp_err_t queue_error;
static relay_sample_t sent[100];
int64_t esp_timer_get_time(void){return clock_us;}
esp_err_t node_identity_get_id_hex(char *id,size_t size){assert(size==13);strcpy(id,"001122334455");return 0;}
esp_err_t node_messages_publish_ble_relay(const char *node,const char *addr,int rssi,
 const uint8_t *mf,size_t ml,const uint8_t *svc,size_t sl){
 assert(!strcmp(node,"001122334455")&&strlen(addr)==12);++attempts;
 if(queue_error)return queue_error;
 assert(accepted<100);relay_sample_t *sample=&sent[accepted++];memset(sample,0,sizeof(*sample));
 memcpy(sample->manufacturer_data,mf,ml);sample->manufacturer_length=ml;
 memcpy(sample->service_data,svc,sl);sample->service_length=sl;sample->rssi=rssi;state_hint(sample);return 0;
}
static void reset(void){memset(relay_device_slots,0,sizeof(relay_device_slots));attempts=accepted=0;queue_error=0;clock_us=0;}
static void observe(unsigned device,int64_t ms,const uint8_t *mf,size_t ml,const uint8_t *svc,size_t sl){
 uint8_t addr[6]={(uint8_t)device,1,2,3,4,5};clock_us=ms*1000;
 switchbot_relay_handle_observation(addr,-(int)(ms%80),mf,ml,svc,sl);
}
int main(void){
 uint8_t env[12]={0}, motion[10]={0},contact[13]={0};motion[7]=0x2c;contact[7]=0x4c;
 reset();observe(1,0,env,12,NULL,0);assert(accepted==1);
 for(unsigned i=1;i<25;i++){env[9]=i;observe(1,i*1000,env,12,NULL,0);}assert(accepted==1);
 observe(1,25000,env,12,NULL,0);assert(accepted==2); // changed counters/RSSI aren't events
 reset();observe(1,0,motion,10,NULL,0);assert(accepted==1&&sent[0].state==0);
 motion[9]++;observe(1,100,motion,10,NULL,0);assert(accepted==1);
 motion[7]=0x6c;observe(1,200,motion,10,NULL,0);assert(accepted==2&&sent[1].state==1);
 motion[7]=0x2c;observe(1,300,motion,10,NULL,0);assert(accepted==3&&sent[2].state==0);
 observe(1,10299,motion,10,NULL,0);assert(accepted==3);
 observe(1,10300,motion,10,NULL,0);assert(accepted==4);
 reset();observe(1,0,contact,13,NULL,0);contact[7]=0x5c;observe(1,100,contact,13,NULL,0);
 assert(accepted==2&&sent[1].state==1);contact[7]=0x6c;observe(1,200,contact,13,NULL,0);
 assert(accepted==2);contact[7]=0xcc;observe(1,300,contact,13,NULL,0);assert(accepted==3&&sent[2].state==0);
 // A failed open observation must survive a close before the next retry.
 reset();motion[7]=0x2c;observe(1,0,motion,10,NULL,0);queue_error=ESP_ERR_NO_MEM;
 motion[7]=0x6c;observe(1,100,motion,10,NULL,0);assert(accepted==1&&attempts==2);
 motion[7]=0x2c;observe(1,200,motion,10,NULL,0);assert(attempts==2);
 assert(relay_device_slots[0].pending_count==2);
 queue_error=0;observe(1,1100,motion,10,NULL,0);
 assert(accepted==3&&sent[1].state==1&&sent[2].state==0&&relay_device_slots[0].pending_count==0);
 // An ordinary failed enqueue does not consume the 25-second interval either.
 reset();queue_error=ESP_FAIL;observe(1,0,env,12,NULL,0);assert(!accepted);
 queue_error=0;observe(1,999,env,12,NULL,0);assert(attempts==1);
 observe(1,1000,env,12,NULL,0);assert(accepted==1);
 // Fragment completion is forwarded once; manufacturer and service expire separately.
 reset();uint8_t svc[3]={0x54,0,50};observe(1,0,env,12,NULL,0);
 observe(1,100,NULL,0,svc,3);assert(accepted==2&&sent[1].manufacturer_length==12&&sent[1].service_length==3);
 observe(1,500,env,12,NULL,0);observe(1,1500,env,12,NULL,0);observe(1,2500,env,12,NULL,0);
 assert(relay_device_slots[0].latest.service_length==0);
 observe(1,25100,env,12,NULL,0);assert(accepted==3&&sent[2].service_length==0);
 // Service type fixes classification, manufacturer state has Gateway's precedence.
 reset();uint8_t motion_svc[6]={0x73,0,50,0,0,1};motion[7]=0x2c;
 observe(1,0,motion,10,motion_svc,6);motion[7]=0x6c;observe(1,100,motion,10,NULL,0);
 assert(accepted==2&&sent[1].state==1);
 reset();uint8_t contact_svc[9]={0x64,0,50,0};observe(1,0,NULL,0,contact_svc,9);
 contact_svc[3]=2;observe(1,100,NULL,0,contact_svc,9);assert(accepted==2&&sent[1].state==1);
 contact_svc[3]=4;observe(1,200,NULL,0,contact_svc,9);assert(accepted==2);
 // Existing Gateway Presence Sensor Pro fixture (test_registry_and_decoder.py).
 // Both service encodings must preserve absent -> present -> absent below 25s.
 uint8_t presence[12]={0x02,0,0,0,0,0x03,0x20,0x8c,0,0x04,0,0x8c};
 uint8_t presence_svc[7]={0x00,0x20,0x64,0x01,0x10,0xcc,0xc8};
 for(unsigned encoding=0;encoding<2;encoding++) {
   reset();presence_svc[0]=encoding?0x70:0x00;presence[7]=0x8c;
   observe(1,0,presence,12,presence_svc,7);
   presence[6]=0x1b;presence[7]=0xcc;presence[9]=0x08;
   observe(1,1000,presence,12,presence_svc,7);
   presence[7]=0x8c;observe(1,5000,presence,12,presence_svc,7);
   assert(accepted==3&&sent[0].state==0&&sent[1].state==1&&sent[2].state==0);
   assert(sent[1].manufacturer_data[7]==0xcc&&sent[2].manufacturer_data[7]==0x8c);
   presence[6]++;observe(1,6000,presence,12,presence_svc,7);assert(accepted==3);
 }
 // An unrelated service remains periodic even with a Presence-shaped payload.
 reset();presence_svc[0]=0x01;observe(1,0,presence,12,presence_svc,7);
 presence[7]=0xcc;observe(1,1000,presence,12,presence_svc,7);assert(accepted==1);
 // Pending slots and age are finite; old state is never replayed past expiry.
 reset();motion[7]=0x2c;observe(1,0,motion,10,NULL,0);queue_error=ESP_FAIL;
 motion[7]=0x6c;observe(1,100,motion,10,NULL,0);motion[7]=0x2c;observe(1,200,motion,10,NULL,0);
 motion[7]=0x6c;observe(1,300,motion,10,NULL,0);assert(relay_device_slots[0].pending_count==2);
 queue_error=0;observe(1,30400,motion,10,NULL,0);assert(accepted==2&&sent[1].state==1);
 reset();for(unsigned i=0;i<16;i++)observe(i,0,env,12,NULL,0);assert(accepted==16);
 observe(17,1000,env,12,NULL,0);assert(accepted==16);
 observe(17,60000,env,12,NULL,0);assert(accepted==17&&relay_device_slots[0].address[0]==17);
 return 0;
}
''')
    binary = tmp_path / "ble-test"
    result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
                             "-I", str(tmp_path), "-I", str(ROOT / "src"),
                             str(tmp_path / "test.c"), "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(binary)], check=True, timeout=10)
