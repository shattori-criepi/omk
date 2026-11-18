#include "ble_scan_trial.h"
#include <string.h>
#include <limits.h>

uint16_t ab_window(uint8_t p) { static const uint16_t w[] = {80,64,40,24}; return p < AB_PRESETS ? w[p] : 0; }
bool ab_mac_valid(const uint8_t m[6]) { static const uint8_t zero[6]; return !(m[0]&1U) && memcmp(m,zero,6); }
void ab_scan_init(ab_scan_t *s) { memset(s,0,sizeof(*s)); s->configured=AB_UNKNOWN; s->known=true; s->segment=4; }
static bool linked(const ab_scan_t *s) {
    const ab_topology_t *t=&s->topology;
    return t->started && t->parent && !t->root && !t->rootless && !t->voting && ab_mac_valid(t->root_mac);
}
static void invalidate(ab_scan_t *s, uint8_t result) {
    s->plan_state=AB_PLAN_FINISHED; s->desired=0; s->segment=4; s->stable=false; s->result=result;
}
void ab_scan_role(ab_scan_t *s, bool root) {
    if (s->topology.root != root) {
        ++s->change; s->topology.root=root;
        if (root) invalidate(s,AB_ROOT_CHANGED);
    }
}
void ab_scan_topology(ab_scan_t *s, const ab_topology_t *t, uint64_t now) {
    if (s->topology.started!=t->started || s->topology.parent!=t->parent ||
        s->topology.root!=t->root || s->topology.rootless!=t->rootless ||
        s->topology.voting!=t->voting || s->topology.link_epoch!=t->link_epoch ||
        memcmp(s->topology.root_mac,t->root_mac,6)) ++s->change;
    s->topology=*t;
    if (s->plan_state!=AB_PLAN_ACTIVE && s->plan_state!=AB_PLAN_SUSPENDED) return;
    if (!t->started || t->root || t->root_changes!=s->bound_changes ||
        (ab_mac_valid(t->root_mac) && memcmp(t->root_mac,s->bound_root,6))) {
        invalidate(s,AB_ROOT_CHANGED); return;
    }
    if (!linked(s)) {
        s->plan_state=AB_PLAN_SUSPENDED; s->desired=0; s->stable=false;
    } else if (s->plan_state==AB_PLAN_SUSPENDED && !s->stable) {
        s->stable=true; s->stable_since=now;
    }
}
static bool same_plan(const ab_plan_t *a,const ab_plan_t *b) {
    if(a->epoch!=b->epoch || a->nonce!=b->nonce || a->cancel!=b->cancel ||
       a->count!=b->count || a->end_ms!=b->end_ms || a->accept_before_ms!=b->accept_before_ms) return false;
    for(unsigned i=0;i<a->count;i++) if(a->segments[i].start_ms!=b->segments[i].start_ms ||
        a->segments[i].end_ms!=b->segments[i].end_ms || a->segments[i].preset!=b->segments[i].preset) return false;
    return true;
}
enum ab_result ab_scan_accept(ab_scan_t *s,const ab_plan_t *p,uint64_t now) {
    if(!p->epoch || p->epoch<s->max_epoch) return AB_OLD_EPOCH;
    /* A duplicate reports prior state even after expiry; never resurrect it. */
    if(p->epoch==s->max_epoch) return same_plan(p,&s->plan)?AB_DUPLICATE:AB_CONFLICT;
    if(!linked(s)) return AB_BAD_ROOT;
    if(p->accept_before_ms<=now || p->accept_before_ms-now>AB_ACCEPT_MS) return AB_BAD_TIME;
    if(!p->cancel) {
        if(s->failed || s->operation!=AB_OP_NONE ||
           s->plan_state==AB_PLAN_ACTIVE || s->plan_state==AB_PLAN_SUSPENDED) return AB_BUSY;
        if(!p->count || p->count>AB_MAX_SEGMENTS || p->end_ms<=now || p->end_ms-now>AB_PLAN_MS) return AB_BAD_TIME;
        uint64_t previous=p->accept_before_ms;
        for(unsigned i=0;i<p->count;i++) {
            const ab_segment_t *g=&p->segments[i];
            if(g->preset>=AB_PRESETS || g->start_ms<previous || (!i && g->start_ms==previous) || g->end_ms<=g->start_ms) return AB_BAD_TIME;
            previous=g->end_ms;
        }
        if(previous!=p->end_ms) return AB_BAD_TIME;
    }
    s->plan=*p; s->max_epoch=p->epoch;
    memcpy(s->bound_root,s->topology.root_mac,6); s->bound_changes=s->topology.root_changes;
    s->plan_state=p->cancel?AB_PLAN_FINISHED:AB_PLAN_ACTIVE;
    s->stable=false; s->desired=0; s->segment=4;
    s->result=p->cancel?AB_CANCELLED:AB_ACCEPTED;
    return (enum ab_result)s->result;
}
uint8_t ab_scan_effective(const ab_scan_t *s) {
    return !s->topology.root && s->known && s->running && !s->operation && !s->timed_out ? s->configured:AB_UNKNOWN;
}
void ab_scan_ready(ab_scan_t *s) { s->ready=true; }
static void target(ab_scan_t *s,uint64_t now) {
    s->desired=0; s->segment=4;
    if(s->plan_state!=AB_PLAN_ACTIVE && s->plan_state!=AB_PLAN_SUSPENDED) return;
    if(now>=s->plan.end_ms) { invalidate(s,AB_EXPIRED); return; }
    if(s->plan_state==AB_PLAN_SUSPENDED) {
        if(!linked(s) || !s->stable || now<s->stable_since || now-s->stable_since<AB_STABLE_MS ||
           ab_scan_effective(s)!=0) return;
        s->plan_state=AB_PLAN_ACTIVE;
    }
    for(unsigned i=0;i<s->plan.count;i++) if(now>=s->plan.segments[i].start_ms && now<s->plan.segments[i].end_ms) {
        s->segment=(uint8_t)i; s->desired=s->plan.segments[i].preset; break;
    }
}
enum ab_op ab_scan_tick(ab_scan_t *s,uint64_t now) {
    target(s,now);
    if(s->operation) {
        if(!s->timed_out && now>=s->op_since && now-s->op_since>=AB_GAP_MS) {
            s->timed_out=true; s->known=false; invalidate(s,AB_GAP_TIMEOUT); ++s->change;
        }
        return AB_OP_NONE;
    }
    if(!s->ready || s->failed) return AB_OP_NONE;
    uint8_t wanted=s->restoring?0:s->desired;
    enum ab_op op=AB_OP_NONE;
    if(s->running && (s->topology.root || s->configured!=wanted)) op=AB_OP_STOP;
    else if(!s->topology.root && !s->running) op=s->configured==wanted?AB_OP_START:AB_OP_PARAM;
    if(op) { s->operation=(uint8_t)op; s->op_preset=wanted; s->op_since=now; ++s->change; }
    return op;
}
void ab_scan_complete(ab_scan_t *s,enum ab_op op,bool ok,uint64_t now) {
    if(!op || s->operation!=op) return; /* unsolicited/late different operation */
    s->operation=AB_OP_NONE; ++s->change;
    bool late=s->timed_out; s->timed_out=false;
    if(ok) {
        s->known=true;
        if(op==AB_OP_STOP) s->running=false;
        if(op==AB_OP_PARAM) s->configured=s->op_preset;
        if(op==AB_OP_START) { s->running=true; s->applied_ms=now; if(!late && !s->restoring) s->result=AB_APPLIED; }
        if(late) { invalidate(s,AB_GAP_TIMEOUT); s->restoring=true; }
        if(op==AB_OP_START && s->configured==0 && s->restoring) s->restoring=false;
        return;
    }
    invalidate(s,AB_GAP_FAILURE);
    /* Failed STOP cannot establish a stopped controller. No retry loop. */
    if(op==AB_OP_STOP || s->restoring) { s->failed=true; return; }
    s->restoring=true; s->configured=AB_UNKNOWN; s->running=false; s->known=true;
}
