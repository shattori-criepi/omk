#include "ble_scan_metrics.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
_Static_assert(52+AB_PRESETS*42+14+6+8+8==AB_RECORD_SIZE,"measurement offsets");
static void inc(uint32_t *v) { if(*v<UINT32_MAX) ++*v; }
void ab_metrics_init(ab_metrics_t *m) { memset(m,0,sizeof(*m)); }
static uint64_t elapsed(uint64_t now,uint64_t since) { return now>=since?now-since:0; }
void ab_metrics_context(ab_metrics_t *m,bool active,bool terminal,uint64_t now) {
    if(terminal && m->mode) {
        m->stopped_ms+=elapsed(now,m->onset); m->ended=now; m->mode=0; m->censored=true;
    }
    if(m->context!=active) { m->watch=false; m->first_demand=now; m->last_ok=0; }
    m->context=active;
}
void ab_metrics_demand(ab_metrics_t *m,uint64_t now) {
    if(!m->watch) { m->first_demand=now; m->watch=true; }
    m->last_demand=now; m->demanded=true;
}
void ab_metrics_tick(ab_metrics_t *m,uint64_t now) {
    if(!m->context) return;
    bool demand=m->watch && (elapsed(now,m->last_demand)<=180000 ||
        (m->sdk_active && m->sdk_normal));
    if(!demand) { m->watch=false; return; }
    uint64_t ref=m->last_ok>m->first_demand?m->last_ok:m->first_demand;
    if(elapsed(now,ref)>=180000) {
        if(!m->mode) { inc(&m->episodes); m->onset=ref+180000; m->ended=0; m->censored=false; }
        m->mode=1; m->recovering=0;
    } else if(m->mode==2 && elapsed(now,m->recovering)>=300000) {
        m->ended=m->recovering; m->stopped_ms+=elapsed(m->ended,m->onset); m->mode=0;
    }
}
void ab_metrics_begin(ab_metrics_t *m,const ab_scan_t *s,uint8_t kind,bool upstream,uint64_t us) {
    m->sdk_active=true; m->sdk_us=us; m->sdk_kind=kind; m->sdk_change=s->change;
    m->sdk_preset=ab_scan_effective(s); m->sdk_episode=m->episodes;
    m->sdk_normal=upstream && kind>=1 && kind<=5 && !s->topology.root;
    m->sdk_valid=m->sdk_normal && m->sdk_preset<4;
    m->sdk_outside=m->context && !m->mode;
}
static unsigned bucket(int32_t e) {
    switch(e) {case 0x400a:return 0;case 0x4007:return 1;case 0x400d:return 2;
        case 0x4017:return 3;case 0x400b:return 4;case 0x400f:return 5;default:return 6;}
}
void ab_metrics_end(ab_metrics_t *m,const ab_scan_t *s,int32_t error,uint64_t us) {
    if(!m->sdk_active) return;
    uint64_t now=us/1000, duration=elapsed(us,m->sdk_us);
    ab_metrics_tick(m,now);
    if(m->sdk_kind==8) { inc(error?&m->self_error:&m->self_ok); }
    else if(m->sdk_normal) {
        bool pure=m->sdk_valid && m->sdk_change==s->change && !s->topology.root;
        if(pure) {
            uint32_t *b=m->bank[m->sdk_preset];
            unsigned bin=duration<20000?0:duration<200000?1:duration<2000000?2:duration<5000000?3:4;
            inc(&b[bin]); if(error) inc(&b[5+bucket(error)]);
            if(m->sdk_outside && m->context && !m->mode && m->episodes==m->sdk_episode) {
                inc(&b[12]); if(error==0x400a) inc(&b[13]);
            }
        } else if(m->sdk_valid) inc(&m->mixed[m->sdk_preset]); else inc(&m->invalid);
        if(!error && m->context && !s->topology.root && m->sdk_change==s->change) {
            m->last_ok=now;
            if(m->mode==1) { m->mode=2; m->recovering=now; }
        }
    }
    m->sdk_active=false;
}
void ab_metrics_drop(ab_metrics_t *m) { inc(&m->self_drop); }
static void put(uint8_t *b,uint64_t v,unsigned n) { for(unsigned i=0;i<n;i++){ b[i]=(uint8_t)v;v>>=8; } }
static uint32_t clamp(uint64_t v,uint32_t max) { return v>max?max:(uint32_t)v; }
void ab_report_pack(const ab_scan_t *s,const ab_metrics_t *m,const ab_report_aux_t *a,uint8_t out[256]) {
    memset(out,0,256); uint32_t flags=0; uint64_t mixed=m->invalid;
    for(unsigned p=0;p<4;p++) {
        mixed+=m->mixed[p]; if(m->mixed[p]) flags|=1U<<(4+p);
        for(unsigned j=0;j<14;j++) { uint32_t v=m->bank[p][j]; if(v>=0xffffff)flags|=1U<<p;
            put(out+52+p*42+j*3,clamp(v,0xffffff),3); }
    }
    if(m->invalid || mixed>=UINT32_MAX) flags|=AB_FLAG_INVALID;
    if(m->self_ok>=65535 || m->self_error>=65535 || m->self_drop>=65535) flags|=AB_FLAG_SELF_SAT;
    if(m->episodes>=65535) flags|=AB_FLAG_EPISODE_SAT;
    if(a->heap_stale) flags|=AB_FLAG_HEAP_STALE;
    if(a->flash_stale) flags|=AB_FLAG_FLASH_STALE;
    if(a->nonce_valid) flags|=AB_FLAG_NONCE;
    if(m->mode==1) flags|=AB_FLAG_STOPPED;
    if(m->mode==2) flags|=AB_FLAG_RECOVERING;
    if(m->censored) flags|=AB_FLAG_CENSORED;
    put(out,a->now_ms,8); put(out+8,s->applied_ms,8); put(out+16,s->plan.end_ms,8);
    put(out+24,s->max_epoch,4); put(out+28,a->nonce,8);
    put(out+36,m->sdk_active?clamp(elapsed(a->now_ms,m->sdk_us/1000),UINT32_MAX):0,4);
    unsigned gap=s->timed_out?6:s->failed?5:s->operation?s->operation:!s->known?6:s->running?4:0;
    uint32_t state=1U | ((uint32_t)s->desired<<4) | ((uint32_t)ab_scan_effective(s)<<7) |
        ((uint32_t)(s->topology.root?1:0)<<10) | (gap<<12) | ((uint32_t)s->plan_state<<15) |
        ((uint32_t)s->segment<<18) | ((uint32_t)(m->sdk_active?m->sdk_kind:0)<<21) | ((uint32_t)s->result<<25);
    put(out+40,state,4); put(out+44,flags,4); put(out+48,clamp(mixed,UINT32_MAX),4);
    put(out+220,clamp(m->episodes,65535),2); put(out+222,clamp(m->onset/1000,UINT32_MAX),4);
    put(out+226,clamp(m->ended/1000,UINT32_MAX),4);
    uint64_t stopped=m->stopped_ms+(m->mode?elapsed(m->mode==2?m->recovering:a->now_ms,m->onset):0);
    put(out+230,clamp(stopped/1000,UINT32_MAX),4);
    put(out+234,clamp(m->self_ok,65535),2);put(out+236,clamp(m->self_error,65535),2);put(out+238,clamp(m->self_drop,65535),2);
    put(out+240,a->heap_free,4);put(out+244,a->heap_min,4);put(out+248,a->arm,4);
    out[252]=a->flash_masks;out[253]=a->flash_phase;out[254]=a->flash_error;out[255]=a->flash_age;
}
size_t ab_report_json(const uint8_t data[256],const char *node,const char *boot,uint32_t seq,uint32_t revision,char *out,size_t cap) {
    static const char digits[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    if(!out || !node || !boot || strlen(node)!=12 || strlen(boot)!=16 || cap<AB_JSON_MAX+1) return 0;
    int n=snprintf(out,cap,"{\"protocol_version\":2,\"node_id\":\"%s\",\"boot_id\":\"%s\",\"seq\":%" PRIu32 ",\"revision\":%" PRIu32 ",\"m\":\"",node,boot,seq,revision);
    if(n<0 || (size_t)n+347>=cap) return 0;
    size_t at=(size_t)n;
    for(unsigned i=0;i<256;i+=3) {
        unsigned left=256-i; uint32_t v=(uint32_t)data[i]<<16;
        if(left>1)v|=(uint32_t)data[i+1]<<8;
        if(left>2)v|=data[i+2];
        out[at++]=digits[v>>18];out[at++]=digits[(v>>12)&63];
        out[at++]=left>1?digits[(v>>6)&63]:'=';out[at++]=left>2?digits[v&63]:'=';
    }
    out[at++]='"';out[at++]='}';out[at]=0;return at<=AB_JSON_MAX?at:0;
}
