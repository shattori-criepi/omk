#pragma once
#include "ble_scan_trial.h"
#define AB_COUNTERS 14U
#define AB_FLAG_INVALID (1U<<8)
#define AB_FLAG_SELF_SAT (1U<<9)
#define AB_FLAG_EPISODE_SAT (1U<<10)
#define AB_FLAG_HEAP_STALE (1U<<11)
#define AB_FLAG_FLASH_STALE (1U<<12)
#define AB_FLAG_NONCE (1U<<13)
#define AB_FLAG_STOPPED (1U<<14)
#define AB_FLAG_RECOVERING (1U<<15)
#define AB_FLAG_CENSORED (1U<<16)

typedef struct {
    uint32_t bank[AB_PRESETS][AB_COUNTERS], mixed[AB_PRESETS], invalid;
    uint32_t self_ok,self_error,self_drop,episodes;
    uint64_t sdk_us, first_demand, last_demand, last_ok, onset, ended, stopped_ms, recovering;
    uint32_t sdk_change,sdk_episode;
    uint8_t sdk_kind,sdk_preset,mode;
    bool sdk_active,sdk_normal,sdk_valid,sdk_outside,watch,context,censored,demanded;
} ab_metrics_t;
typedef struct {
    uint64_t now_ms, nonce;
    uint32_t heap_free,heap_min,arm;
    uint8_t flash_masks,flash_phase,flash_error,flash_age;
    bool heap_stale,flash_stale,nonce_valid;
} ab_report_aux_t;
void ab_metrics_init(ab_metrics_t *m);
void ab_metrics_context(ab_metrics_t *m,bool active,bool terminal,uint64_t now);
void ab_metrics_demand(ab_metrics_t *m,uint64_t now);
void ab_metrics_tick(ab_metrics_t *m,uint64_t now);
void ab_metrics_begin(ab_metrics_t *m,const ab_scan_t *s,uint8_t kind,bool upstream,uint64_t us);
void ab_metrics_end(ab_metrics_t *m,const ab_scan_t *s,int32_t error,uint64_t us);
void ab_metrics_drop(ab_metrics_t *m);
void ab_report_pack(const ab_scan_t *s,const ab_metrics_t *m,const ab_report_aux_t *a,uint8_t out[AB_RECORD_SIZE]);
size_t ab_report_json(const uint8_t data[AB_RECORD_SIZE],const char *node,const char *boot,
                      uint32_t seq,uint32_t revision,char *out,size_t capacity);
