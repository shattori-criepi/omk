"""Execute production netif callbacks/worker with a bounded queue and fake radio.

The scheduler is deterministic; no claim about RF throughput or libmesh internals.
"""
from pathlib import Path
import subprocess

PROJECT = Path(__file__).parents[1]


def test_copied_frames_pressure_role_changes_and_radio_failures(tmp_path):
    source = (PROJECT / "src/mesh_netif.c").read_text()
    sender = source[source.index("/* Copy Ethernet frames"):source.index("static esp_err_t attach_root_ap_driver")]
    program = r'''
#include <assert.h>
#define OMK_NETWORK_DIAGNOSTICS 1
#include "network_diagnostics.h"
static unsigned rejected[ND_REJECTIONS];
void network_diag_rejected(unsigned reason) {assert(reason<ND_REJECTIONS);++rejected[reason];}
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <setjmp.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE -1
#define ESP_ERR_INVALID_SIZE -2
#define ESP_ERR_NO_MEM -3
#define MESH_NETIF_RX_SIZE 1560
#define MESH_MAC_LENGTH 6
#define CONFIG_MESH_ROUTE_TABLE_SIZE 4
#define MESH_PROTO_STA 1
#define MESH_PROTO_AP 2
#define MESH_TOS_P2P 1
#define MESH_DATA_P2P 4
#define MESH_DATA_TODS 8
#define pdTRUE 1
#define portMAX_DELAY UINT32_MAX
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) assert(++*(p)==1)
#define portEXIT_CRITICAL(p) assert(--*(p)==0)
typedef struct {uint8_t addr[6];} mesh_addr_t;
typedef struct {uint8_t *data;size_t size;int proto,tos;} mesh_data_t;
typedef struct {uint8_t station_mac[6];} driver_t, *mesh_netif_driver_t;
typedef int StaticQueue_t;
typedef void *QueueHandle_t;
static mesh_addr_t routing_table[4];
static unsigned indices[4], queued, head, sends, failures, captured_op, visited;
static uint8_t captured[1560];
static bool root_role, radio_changes_role, radio_stalls, deny_queue;
static uint64_t now;
static int radio_result;
static jmp_buf idle;
static bool esp_mesh_is_root(void) {return root_role;}
static int64_t esp_timer_get_time(void) {return now;}
static int xQueueSend(void *q,const unsigned *index,unsigned wait) {
    assert(q && wait==0);
    if(deny_queue || queued==4) return 0;
    indices[(head+queued++)%4]=*index;return pdTRUE;
}
static int xQueueReceive(void *q,unsigned *index,unsigned wait) {
    assert(q && wait==portMAX_DELAY);
    if(!queued) longjmp(idle,1); // Return control when the real worker would sleep.
    *index=indices[head++%4];--queued;return pdTRUE;
}
static int esp_mesh_get_routing_table(mesh_addr_t *table,size_t size,int *count) {
    assert(size>=sizeof(routing_table));*count=4;
    for(unsigned i=0;i<4;i++) memset(table[i].addr,i,6);
    return ESP_OK;
}
static int record_mesh_send_result(int result) {if(result) ++failures;return result;}
static int observed_mesh_send(unsigned op,const mesh_addr_t *to,const mesh_data_t *data,int flags) {
    assert(data->tos==MESH_TOS_P2P);
    if(op==ND_MESH_UP) assert(!to && data->proto==MESH_PROTO_AP && flags==MESH_DATA_TODS);
    else {
        assert(to && data->proto==MESH_PROTO_STA && flags==MESH_DATA_P2P);
        if(op==ND_MESH_BROADCAST) visited |= 1U << to->addr[0];
    }
    ++sends;captured_op=op;memcpy(captured,data->data,data->size);
    if(radio_changes_role) root_role=!root_role;
    if(radio_stalls) now+=5000000; // Timeout of one peer cannot extend a broadcast forever.
    return radio_result;
}
''' + sender + r'''
static void drain(void) {if(!setjmp(idle)) transmit_task(NULL);}
int main(void) {
    (void)tx_queue_control;(void)tx_queue_storage;
    uint8_t frame[64];memset(frame,42,sizeof(frame));driver_t driver={0};
    assert(transmit_child_sta(NULL,frame,sizeof(frame))==ESP_ERR_INVALID_STATE);
    tx_queue=(void *)1;
    assert(transmit_child_sta(NULL,frame,0)==ESP_ERR_INVALID_SIZE);
    assert(transmit_child_sta(NULL,frame,1561)==ESP_ERR_INVALID_SIZE);
    for(unsigned i=0;i<4;i++) assert(transmit_child_sta_wrap(NULL,frame,sizeof(frame),NULL)==ESP_OK);
    assert(!sends && transmit_child_sta(NULL,frame,sizeof(frame))==ESP_ERR_NO_MEM);
    assert(rejected[ND_QUEUE_FULL]==1);
    memset(frame,77,sizeof(frame));drain();
    assert(sends==4 && captured[0]==42 && captured_op==ND_MESH_UP); // copied, not borrowed
    unsigned before=sends;
    assert(transmit_child_sta(NULL,frame,sizeof(frame))==ESP_OK);
    invalidate_tx();drain();assert(sends==before); // parent disconnect/reconnect invalidates pending frames
    assert(transmit_child_sta(NULL,frame,sizeof(frame))==ESP_OK);
    root_role=true;drain();assert(sends==before);assert(rejected[ND_FRAME_INVALIDATED]==2); // role changed before event handler runs
    assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);
    drain();assert(sends==before+1 && captured_op==ND_MESH_DOWN);
    memset(frame,255,6);
    assert(transmit_root_ap_wrap(&driver,frame,sizeof(frame),NULL)==ESP_OK);
    drain();assert(sends==before+4 && captured_op==ND_MESH_BROADCAST); // self excluded
    before=sends;radio_changes_role=true;
    assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);
    drain();assert(sends==before+1); // stop remaining fanout at role change
    radio_changes_role=false;root_role=true;radio_stalls=true;radio_result=-7;before=sends;
    assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);
    drain();assert(sends==before+2 && failures==2); // bounded age even with slow peers
    visited=0;
    for(unsigned i=0;i<3;i++) {
        assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);drain();
    }
    assert(visited==14); // every non-self route gets a turn despite per-frame age limit
    radio_stalls=false;radio_result=0;before=sends;
    assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);
    now+=10000000;drain();assert(sends==before);
    assert(rejected[ND_FRAME_EXPIRED]==5 && rejected[ND_FRAME_INVALIDATED]==3);
    deny_queue=true;
    for(unsigned i=0;i<100;i++) assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_ERR_NO_MEM);
    assert(rejected[ND_QUEUE_FULL]==101 && !rejected[ND_BACKOFF] && !rejected[ND_MQTT_BUSY]);
    deny_queue=false;
    for(unsigned i=0;i<4;i++) assert(transmit_root_ap(&driver,frame,sizeof(frame))==ESP_OK);
    drain();assert(sends==before+12); // denied insertions released every pool slot
    return 0;
}
'''
    path = tmp_path / "mesh_tx.c"
    path.write_text(program)
    binary = tmp_path / "mesh_tx"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(PROJECT / "src"), str(path), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    start = source[source.index("esp_err_t mesh_netifs_start"):source.index("esp_err_t mesh_netif_start_root_ap")]
    assert start.count("invalidate_tx();") == 2
    assert '"mesh_netif_tx", 4096' in source
    network = (PROJECT / "src/mesh_network.c").read_text()
    assert "esp_mesh_send_block_time(5000)" in network
