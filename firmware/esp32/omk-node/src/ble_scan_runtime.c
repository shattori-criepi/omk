#include "ble_scan_runtime.h"
#include <string.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
static portMUX_TYPE ab_lock = portMUX_INITIALIZER_UNLOCKED;
static ab_scan_t scan;
static ab_metrics_t metrics;
static ab_report_aux_t auxiliary;
static char identity[13], boot_id[17];
static uint64_t diagnostic_ms;
static bool initialized,diagnostic_valid,dirty;
static uint64_t last_report_ms,last_event_ms;
static uint32_t last_observation;
static bool observed,event_pending,event_logged;
static uint8_t completion_op;
static bool completion_ok;
static uint64_t completion_ms;
static ab_event_t pending_event;
static void event_changed(uint64_t now,uint32_t epoch) {
    pending_event=(ab_event_t){.ms=now,.epoch=epoch,.detail=scan.desired |
        ((uint32_t)ab_scan_effective(&scan)<<3) | ((uint32_t)scan.result<<8)};
    event_pending=true;
}
static uint64_t now_ms(void) { return (uint64_t)esp_timer_get_time()/1000; }
void ab_runtime_probe_context(uint8_t *effective,uint32_t *change) {
    portENTER_CRITICAL(&ab_lock);
    *effective=ab_scan_effective(&scan); *change=scan.change;
    portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_boot(const char *node,const char *boot) {
    portENTER_CRITICAL(&ab_lock);
    if (!initialized) {
        ab_scan_init(&scan); ab_metrics_init(&metrics);
        memcpy(identity,node,13); memcpy(boot_id,boot,17); initialized=true;
    }
    portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_topology(const ab_topology_t *t) {
    uint64_t now=now_ms();
    portENTER_CRITICAL(&ab_lock);
    uint32_t distance=t->observation-last_observation;
    if(observed && (!distance || distance>=UINT32_C(0x80000000))) {
        portEXIT_CRITICAL(&ab_lock); return;
    }
    observed=true; last_observation=t->observation;
    uint8_t plan=scan.plan_state;
    ab_scan_topology(&scan,t,now);
    dirty|=plan!=scan.plan_state;
    if(plan!=scan.plan_state) event_changed(now,scan.max_epoch);
    bool nonce_valid=t->root && t->root_nonce;
    auxiliary.nonce_valid=nonce_valid;
    if(nonce_valid) auxiliary.nonce=t->root_nonce;
    bool active=t->started && t->parent && !t->root && !t->rootless && !t->voting;
    ab_metrics_context(&metrics,active,!t->started || t->root,now);
    if (!t->started || !t->root) auxiliary.nonce_valid=false;
    portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_role(bool root) {
    uint64_t now=now_ms();portENTER_CRITICAL(&ab_lock);
    uint8_t before=scan.plan_state;
    ab_scan_role(&scan,root);dirty|=before!=scan.plan_state && scan.max_epoch!=0;
    if(before!=scan.plan_state && scan.max_epoch) event_changed(now,scan.max_epoch);
    portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_ready(void) {
    portENTER_CRITICAL(&ab_lock); ab_scan_ready(&scan); portEXIT_CRITICAL(&ab_lock);
}
enum ab_op ab_runtime_tick(uint16_t *window) {
    uint64_t now=now_ms();
    portENTER_CRITICAL(&ab_lock);
    ab_metrics_tick(&metrics,now);
    if(completion_op) {
        /* Completion mailbox is consumed by the sole GAP owner. Evaluate a
         * late callback at its actual time before clearing the pending API. */
        ab_scan_tick(&scan,completion_ms);
        uint8_t before=scan.result;
        ab_scan_complete(&scan,(enum ab_op)completion_op,completion_ok,completion_ms);
        dirty=true;
        if(before!=scan.result || completion_op==AB_OP_START) event_changed(completion_ms,scan.max_epoch);
        completion_op=0;
    }
    uint8_t result=scan.result,plan=scan.plan_state,desired=scan.desired,segment=scan.segment;
    enum ab_op op=ab_scan_tick(&scan,now);
    bool changed=result!=scan.result || plan!=scan.plan_state || desired!=scan.desired || segment!=scan.segment;
    dirty|=changed;
    if(changed) event_changed(now,scan.max_epoch);
    *window=ab_window(scan.op_preset);
    portEXIT_CRITICAL(&ab_lock); return op;
}
void ab_runtime_complete(enum ab_op op,bool success) {
    uint64_t now=now_ms();
    portENTER_CRITICAL(&ab_lock);
    if(op && scan.operation==op && !completion_op) {
        completion_op=(uint8_t)op;completion_ok=success;completion_ms=now;
    }
    portEXIT_CRITICAL(&ab_lock);
}
bool ab_runtime_receiving(void) {
    portENTER_CRITICAL(&ab_lock);
    bool valid=ab_scan_effective(&scan)<AB_PRESETS;
    portEXIT_CRITICAL(&ab_lock); return valid;
}
enum ab_result ab_runtime_command(const char *payload,size_t length,const uint8_t source[6]) {
    ab_command_t command;
    bool valid=ab_command_parse(payload,length,&command);
    uint64_t now=now_ms();
    portENTER_CRITICAL(&ab_lock);
    enum ab_result result;
    if (!valid) result=AB_BAD_COMMAND;
    else if (strcmp(command.node_id,identity)||strcmp(command.boot_id,boot_id)) result=AB_WRONG_BOOT;
    else if (!ab_mac_valid(scan.topology.root_mac)||memcmp(source,scan.topology.root_mac,6)) result=AB_BAD_ROOT;
    else result=ab_scan_accept(&scan,&command.plan,now);
    /* Duplicate results never mutate the accepted plan or its end time. */
    uint8_t previous=scan.result;
    scan.result=result; dirty=true;
    if(result!=AB_DUPLICATE && (result!=previous || result==AB_ACCEPTED || result==AB_CANCELLED))
        event_changed(now,valid?command.plan.epoch:scan.max_epoch);
    if(result==AB_ACCEPTED || result==AB_CANCELLED) { auxiliary.nonce=command.plan.nonce; auxiliary.nonce_valid=false; }
    portEXIT_CRITICAL(&ab_lock); return result;
}
void ab_runtime_demand(void) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    ab_metrics_demand(&metrics,now); portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_sdk_begin(uint8_t kind,bool upstream) {
    uint64_t us=esp_timer_get_time(); portENTER_CRITICAL(&ab_lock);
    ab_metrics_begin(&metrics,&scan,kind,upstream,us); portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_sdk_end(int32_t error) {
    uint64_t us=esp_timer_get_time(); portENTER_CRITICAL(&ab_lock);
    ab_metrics_end(&metrics,&scan,error,us); portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_drop(void) {
    portENTER_CRITICAL(&ab_lock); ab_metrics_drop(&metrics); portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_pack(uint8_t out[AB_RECORD_SIZE]) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    auxiliary.now_ms=now;
    auxiliary.heap_stale=auxiliary.flash_stale=!diagnostic_valid || now-diagnostic_ms>120000;
    uint64_t age=diagnostic_valid?(now-diagnostic_ms)/1000:255;
    auxiliary.flash_age=age>255?255:(uint8_t)age;
    ab_metrics_tick(&metrics,now);
    ab_report_pack(&scan,&metrics,&auxiliary,out);
    portEXIT_CRITICAL(&ab_lock);
}
void ab_runtime_diagnostic(uint32_t free_heap,uint32_t min_heap,uint32_t arm,
                           uint8_t masks,uint8_t phase,uint8_t error) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    auxiliary.heap_free=free_heap; auxiliary.heap_min=min_heap; auxiliary.arm=arm;
    auxiliary.flash_masks=masks; auxiliary.flash_phase=phase; auxiliary.flash_error=error;
    diagnostic_ms=now; diagnostic_valid=true;
    portEXIT_CRITICAL(&ab_lock);
}

bool ab_runtime_report_due(void) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    bool due=dirty && now>=last_report_ms && now-last_report_ms>=10000;
    portEXIT_CRITICAL(&ab_lock); return due;
}
void ab_runtime_reported(void) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    dirty=false; last_report_ms=now; portEXIT_CRITICAL(&ab_lock);
}

bool ab_runtime_periodic_allowed(void) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    bool recent=metrics.demanded && now>=metrics.last_demand && now-metrics.last_demand<=180000;
    portEXIT_CRITICAL(&ab_lock); return recent;
}

bool ab_runtime_event(ab_event_t *event) {
    uint64_t now=now_ms(); portENTER_CRITICAL(&ab_lock);
    bool take=event_pending && (!event_logged || now-last_event_ms>=10000);
    if(take) {*event=pending_event;event_pending=false;last_event_ms=now;event_logged=true;}
    portEXIT_CRITICAL(&ab_lock);return take;
}
