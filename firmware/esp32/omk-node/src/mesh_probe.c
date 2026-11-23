#include "mesh_probe.h"
#if defined(OMK_MESH_PROBE) && OMK_MESH_PROBE && OMK_DIAGNOSTICS
#include "mesh_transport.h"
#include "ble_scan_runtime.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include <string.h>
#if !CONFIG_IDF_TARGET_ESP32S3 || ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(6,0,1) || CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM != 32 || !CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER
#error Private TX probe requires the audited ESP32-S3 IDF 6.0.1 dynamic-32 ABI
#endif
/* Bind actual linker symbols, never ROM *_ptr aliases or absolute addresses.
 * Aligned Xtensa loads observe SDK mutex-protected writers without taking that
 * private mutex. Individual fields are coherent; a group is not atomic. */
extern unsigned char g_eb_list_desc[], g_lmac_cnt[];
static portMUX_TYPE probe_lock=portMUX_INITIALIZER_UNLOCKED;
static mesh_probe_core_t probe;
static uint64_t last_poll_us;
static bool initialized;
static inline void barrier(void) {
#ifdef __XTENSA__
    __asm__ volatile("memw" ::: "memory");
#else
    __asm__ volatile("" ::: "memory"); /* host tests of the production sampler */
#endif
}
static uint16_t read16(const unsigned char *p) { return *(const volatile uint16_t *)(const void *)p; }
static uint32_t read32(const unsigned char *p) { return *(const volatile uint32_t *)(const void *)p; }
static mesh_probe_raw_t raw_once(void) {
    mesh_probe_raw_t r={0};
    r.us=(uint64_t)esp_timer_get_time();
    barrier();
    r.alloc_raw=read32(g_lmac_cnt+12); r.recycled=read32(g_lmac_cnt+16);
    r.used=read16(g_eb_list_desc+34); r.limit=read16(g_eb_list_desc+36);
    r.malloc_failed=read16(g_lmac_cnt+20); r.rejected=read16(g_lmac_cnt+22);
    barrier();
    if(*(volatile unsigned char *)(g_eb_list_desc+28)==1 && r.limit==32 && r.used<=32) r.flags=PROBE_VALID;
    return r;
}
static mesh_probe_raw_t raw(void) {
    mesh_probe_raw_t a=raw_once(),b=raw_once();
    if(a.alloc_raw!=b.alloc_raw || a.recycled!=b.recycled || a.used!=b.used ||
       a.malloc_failed!=b.malloc_failed || a.rejected!=b.rejected || a.limit!=b.limit) b.flags|=PROBE_CHANGING;
    if(!(a.flags&PROBE_VALID)) b.flags&=~PROBE_VALID;
    return b; /* exactly two reads; no consistency retry loop */
}
static mesh_probe_event_t event(uint8_t kind,uint32_t epoch,unsigned phase,int32_t result) {
    mesh_probe_event_t e={0}; mesh_transport_status_t s;
    mesh_transport_get_status(&s);
    uint8_t effective=255; ab_runtime_probe_context(&effective,&e.scan_change);
    e.raw=raw(); e.epoch=epoch; e.result=result;
    e.meta=kind|((uint32_t)effective<<8)|((uint32_t)s.is_root<<16)|((uint32_t)phase<<24);
    memcpy(e.parent,s.parent_mac,6); memcpy(e.root,s.root_mac,6);
    /* Slow allocator queries only on errors/recovery, outside all app locks.
     * Timing is the SDK bracket plus bounded bookkeeping, not RF ACK time. */
    if(result || phase) {
        e.reserved[0]=heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        e.reserved[1]=heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_DMA|MALLOC_CAP_8BIT);
        e.reserved[2]=heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_DMA|MALLOC_CAP_8BIT);
    }
    return e;
}
void mesh_probe_boot(const char *node,const char *boot) {
    portENTER_CRITICAL(&probe_lock);
    if(!initialized) {
        memset(&probe,0,sizeof(probe)); probe.version=1; probe.bytes=sizeof(probe);
        memcpy(probe.node,node,13); memcpy(probe.boot,boot,17); initialized=true;
    }
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_begin(uint8_t kind,uint32_t epoch) {
    mesh_probe_event_t e=event(kind,epoch,0,0);
    portENTER_CRITICAL(&probe_lock);
    e.call_id=++probe.call_sequence; probe.events[0]=e; probe.seen|=8;
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_end(int32_t result,uint32_t epoch) {
    mesh_probe_event_t e=event(0,epoch,0,result);
    portENTER_CRITICAL(&probe_lock);
    const mesh_probe_event_t *before=&probe.events[0];
    e.call_id=before->call_id; e.meta|=before->meta&255;
    uint64_t duration=e.raw.us>=before->raw.us?e.raw.us-before->raw.us:UINT64_MAX;
    e.duration_us=duration>UINT32_MAX?UINT32_MAX:(uint32_t)duration;
    if(e.epoch!=before->epoch || e.scan_change!=before->scan_change ||
       (e.meta&0x1ff00)!=(before->meta&0x1ff00)) e.raw.flags|=PROBE_MIXED;
    if(result==0x400a && !(probe.seen&1)) { probe.events[1]=*before; probe.events[2]=e; probe.seen|=1; }
    if(result==0x4007 && !(probe.seen&2)) { probe.events[3]=*before; probe.events[4]=e; probe.seen|=2; }
    if(result) { probe.events[6]=*before; probe.events[7]=e; }
    if(!(probe.seen&4) && (e.raw.flags&before->raw.flags&PROBE_VALID) && e.raw.rejected!=before->raw.rejected) {
        probe.events[5]=e; probe.seen|=4;
    }
    probe.abi_flags=e.raw.flags;
    probe.seen&=~8U;
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_poll(void) {
    uint64_t now=(uint64_t)esp_timer_get_time();
    if(last_poll_us && now-last_poll_us<1000000) return;
    last_poll_us=now;
    mesh_probe_raw_t r=raw();
    portENTER_CRITICAL(&probe_lock);
    probe.recent[probe.ring_head]=r; probe.ring_head=(probe.ring_head+1)%8;
    if(probe.ring_count<8) ++probe.ring_count; else if(probe.ring_overwrites!=UINT32_MAX) ++probe.ring_overwrites;
    probe.abi_flags=r.flags;
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_recovery(unsigned phase,int32_t result,unsigned subtree_nodes) {
    mesh_probe_event_t e=event(0,0,phase,result);
    unsigned slot=phase==PROBE_BEGIN?8:phase==PROBE_API?9:phase==PROBE_DISCONNECTED?10:phase==PROBE_LINKED?11:12;
    portENTER_CRITICAL(&probe_lock);
    if(phase==PROBE_BEGIN) { ++probe.recovery_attempt; memset(&probe.events[8],0,5*sizeof(e)); }
    e.call_id=(uint32_t)probe.recovery_attempt; probe.events[slot]=e;
    probe.phase_ms=e.raw.us/1000; probe.recovery_phase=phase; probe.subtree_nodes=subtree_nodes;
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_quality(const mesh_quality_view_t *v) {
    portENTER_CRITICAL(&probe_lock);
    uint32_t *p=probe.quality;
    p[0]=v->attempts;p[1]=v->ok;p[2]=v->timeout;p[3]=v->no_memory;p[4]=v->requests;
    p[5]=v->drops;p[6]=v->statuses;p[7]=v->receipts;p[8]=v->reasons;
    p[9]=(v->bad?1:0)|(v->healthy?2:0)|(v->saturated?4:0);
    portEXIT_CRITICAL(&probe_lock);
}
void mesh_probe_copy(void *out) {
    portENTER_CRITICAL(&probe_lock); memcpy(out,&probe,sizeof(probe)); portEXIT_CRITICAL(&probe_lock);
}
#endif
