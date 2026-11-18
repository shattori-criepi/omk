#pragma once
/* Pure bounded policy. The transport lock serializes writers and evaluation.
 * No SDK calls, allocation, logging, wire changes or boot-counter resets. */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <limits.h>

#define MQ_BUCKET_MS UINT64_C(30000)
#define MQ_BUCKETS 11U
#define MQ_PERSIST_MS UINT64_C(60000)
#define MQ_WARMUP_MS UINT64_C(120000)
enum { MQ_SDK_BAD=1, MQ_RESOURCE_BAD=2, MQ_QUEUE_BAD=4, MQ_RECEIPT_BAD=8, MQ_SEVERE=16 };
typedef struct {
    uint64_t tag; /* bucket index + 1; zero is empty */
    uint32_t attempts, ok, timeout, no_memory, requests, drops, statuses, receipts;
} mq_bucket_t;
typedef struct {
    mq_bucket_t bins[MQ_BUCKETS];
    uint64_t started_ms, last_sdk_ms, bad_since_ms, last_error_ms, last_drop_ms;
    uint32_t last_status_seq;
    bool initialized, bad_pending, have_status, saturated;
} mesh_quality_t;
typedef struct {
    uint32_t attempts, ok, timeout, no_memory, requests, drops, statuses, receipts;
    uint32_t reasons;
    bool bad, healthy, saturated;
} mesh_quality_view_t;
static inline void mq_reset(mesh_quality_t *q, uint64_t now) {
    memset(q,0,sizeof(*q)); q->initialized=true; q->started_ms=now;
}
static inline mq_bucket_t *mq_bucket(mesh_quality_t *q,uint64_t now) {
    if(!q->initialized) mq_reset(q,now);
    uint64_t tag=now/MQ_BUCKET_MS+1;
    mq_bucket_t *b=&q->bins[tag%MQ_BUCKETS];
    if(b->tag!=tag) { memset(b,0,sizeof(*b)); b->tag=tag; }
    return b;
}
static inline void mq_inc(mesh_quality_t *q,uint32_t *p) {
    if(*p!=UINT32_MAX) ++*p; else q->saturated=true;
}
static inline void mq_request(mesh_quality_t *q,uint64_t now,bool status,uint32_t seq,uint64_t generated_ms) {
    mq_bucket_t *b=mq_bucket(q,now); mq_inc(q,&b->requests);
    if(status && (!q->have_status || q->last_status_seq!=seq)) {
        if(generated_ms<=now && now-generated_ms<=30000) b=mq_bucket(q,generated_ms);
        mq_inc(q,&b->statuses); q->have_status=true; q->last_status_seq=seq;
    }
}
static inline void mq_drop(mesh_quality_t *q,uint64_t now) { mq_inc(q,&mq_bucket(q,now)->drops); q->last_drop_ms=now; }
static inline void mq_sdk(mesh_quality_t *q,uint64_t now,int32_t result) {
    mq_bucket_t *b=mq_bucket(q,now); mq_inc(q,&b->attempts);
    if(!result) mq_inc(q,&b->ok);
    if(result==0x400a) mq_inc(q,&b->timeout);
    if(result==0x4007) mq_inc(q,&b->no_memory);
    if(result) q->last_error_ms=now;
    q->last_sdk_ms=now;
}
static inline void mq_receipt(mesh_quality_t *q,uint64_t generated_ms,uint64_t now) {
    /* Only validated, monotonically advancing receipts call this function.
     * Credit the offer bucket, not receipt arrival. Never create an old bucket. */
    if(!q->initialized || generated_ms>now || now-generated_ms>120999) return;
    uint64_t tag=generated_ms/MQ_BUCKET_MS+1;
    mq_bucket_t *b=&q->bins[tag%MQ_BUCKETS];
    if(b->tag==tag && b->receipts<b->statuses) mq_inc(q,&b->receipts);
}
static inline uint32_t mq_sum(uint32_t a,uint32_t b,bool *sat) {
    if(UINT32_MAX-a<b) { *sat=true; return UINT32_MAX; } return a+b;
}
static inline mesh_quality_view_t mq_evaluate(mesh_quality_t *q,uint64_t now,bool eligible) {
    mesh_quality_view_t v={0}; v.saturated=q->saturated;
    uint64_t tag=now/MQ_BUCKET_MS+1;
    for(unsigned i=0;i<MQ_BUCKETS;i++) {
        const mq_bucket_t *b=&q->bins[i];
        /* Current partial bucket plus nine complete buckets: 270..300 seconds. */
        if(!b->tag || b->tag>tag || tag-b->tag>=10) continue;
#define MQ_ADD(f) v.f=mq_sum(v.f,b->f,&v.saturated)
        MQ_ADD(attempts); MQ_ADD(ok); MQ_ADD(timeout); MQ_ADD(no_memory); MQ_ADD(requests); MQ_ADD(drops);
        /* Allow every status in a bucket its entire 120 s receipt freshness. */
        if(now>=b->tag*MQ_BUCKET_MS+120999) { MQ_ADD(statuses); MQ_ADD(receipts); }
#undef MQ_ADD
    }
    uint64_t n=v.attempts, errors=n>=v.ok?n-v.ok:0, resource=(uint64_t)v.timeout+v.no_memory;
    if(n>=20 && errors>=8 && errors*100>=n*20) v.reasons|=MQ_SDK_BAD;
    if(n>=20 && resource>=6 && resource*100>=n*15) v.reasons|=MQ_RESOURCE_BAD;
    if(v.requests>=30 && (uint64_t)v.drops*100>=(uint64_t)v.requests*30) v.reasons|=MQ_QUEUE_BAD;
    if(v.statuses>=4 && (uint64_t)v.receipts*100<=(uint64_t)v.statuses*50) v.reasons|=MQ_RECEIPT_BAD;
    if(n>=20 && errors>=12 && errors*100>=n*40) v.reasons|=MQ_SEVERE;
    /* Old bad buckets must not cut a link that has already recovered.
     * Require an ongoing symptom within 30 s throughout the persistence time. */
    bool recent_error=errors && now>=q->last_error_ms && now-q->last_error_ms<=30000;
    bool recent_drop=v.drops && now>=q->last_drop_ms && now-q->last_drop_ms<=30000;
    bool raw=(recent_error && ((v.reasons&MQ_SEVERE) ||
        ((v.reasons&MQ_SDK_BAD) && (v.reasons&(MQ_RESOURCE_BAD|MQ_QUEUE_BAD|MQ_RECEIPT_BAD))))) ||
        (recent_drop && (v.reasons&MQ_QUEUE_BAD) && (v.reasons&MQ_RECEIPT_BAD));
    bool fresh=q->initialized && now>=q->started_ms && now-q->started_ms>=MQ_WARMUP_MS &&
        now>=q->last_sdk_ms && now-q->last_sdk_ms<=60000;
    if(eligible && fresh && raw && !v.saturated) {
        if(!q->bad_pending) { q->bad_pending=true; q->bad_since_ms=now; }
        v.bad=now>=q->bad_since_ms && now-q->bad_since_ms>=MQ_PERSIST_MS;
    } else q->bad_pending=false;
    v.healthy=eligible && fresh && !v.saturated && n>=20 && errors*100<=n*10 &&
        v.requests>=20 && (uint64_t)v.drops*100<=(uint64_t)v.requests*10 &&
        v.statuses>=4 && (uint64_t)v.receipts*100>=(uint64_t)v.statuses*75;
    return v;
}
