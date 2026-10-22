#include "network_diagnostics.h"
#ifdef OMK_NETWORK_DIAGNOSTICS
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "driver/usb_serial_jtag.h"
#include "esp_app_desc.h"
#include "esp_attr.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define ND_MAGIC 0x4e444732U
#define ND_EARLY_SAVE_LIMIT 4U
#define ND_SAVE_LIMIT 16U
#define ND_SAVE_GAP_US (600ULL * 1000000)
#define ND_REPORT_GAP_US (300ULL * 1000000)
typedef struct { uint32_t magic; nd_snapshot_t snapshot; uint32_t crc; } checkpoint_t;
/* Two slots: an interrupted checkpoint never invalidates the previous slot. */
static RTC_NOINIT_ATTR checkpoint_t rtc_checkpoints[2];
/* Fault slots survive normal progress and repeated soft resets. */
static RTC_NOINIT_ATTR checkpoint_t rtc_faults[2];
static unsigned fault_slot;
static portMUX_TYPE view_lock = portMUX_INITIALIZER_UNLOCKED;
static nd_snapshot_t retained, saved;
static uint32_t saved_reasons, saved_critical, saves, sequence, usb_dropped;
static int32_t save_error;
static bool storage_disabled, initialized;
static uint64_t last_save_us, next_report_us;
/* Sender-owned RAM only; independent of observer/RTC/NVS generations. */
static unsigned next_report_view;
static uint32_t live_report_sequence;
static uint64_t flash_started_us, flash_finished_us;
static StaticTask_t observer_tcb;
static StackType_t observer_stack[4096 / sizeof(StackType_t)];

static uint32_t checksum(const void *data, size_t size) {
    const uint8_t *p = data;
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < size; ++i) {
        crc ^= p[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
    }
    return ~crc;
}
static bool valid(const checkpoint_t *c) {
    return c->magic == ND_MAGIC && c->snapshot.schema == ND_SCHEMA &&
           c->snapshot.saves <= ND_SAVE_LIMIT &&
           !(c->snapshot.reasons & ~0x7ffU) &&
           !(c->snapshot.critical_reasons & ~c->snapshot.reasons) &&
           !(c->snapshot.saved_critical & ~c->snapshot.saved_reasons) &&
           c->crc == checksum(&c->snapshot, sizeof(c->snapshot));
}
static void seal(checkpoint_t *c, const nd_snapshot_t *s) {
    c->magic = 0;
    __sync_synchronize();
    c->snapshot = *s;
    c->crc = checksum(&c->snapshot, sizeof(c->snapshot));
    __sync_synchronize();
    c->magic = ND_MAGIC;
}
static void metadata(nd_snapshot_t *s) {
    s->saved_reasons = saved_reasons; s->saved_critical = saved_critical; s->saves = saves;
    s->save_error = save_error; s->usb_dropped = usb_dropped;
    s->flash_started_us = flash_started_us; s->flash_finished_us = flash_finished_us;
}
static bool get_view(unsigned view, nd_snapshot_t *s) {
    if (view == 0) {
        network_diag_snapshot(s);
        s->reasons = network_diag_reasons(s);
        s->critical_reasons = network_diag_critical(s);
        portENTER_CRITICAL(&view_lock);
        metadata(s);
        portEXIT_CRITICAL(&view_lock);
        return true;
    }
    portENTER_CRITICAL(&view_lock);
    *s = view == 1 ? retained : saved;
    portEXIT_CRITICAL(&view_lock);
    return s->schema == ND_SCHEMA;
}
static esp_err_t save_checkpoint(checkpoint_t *c) {
    /* Reserve ample room for existing registration/credentials. Never erase. */
    nvs_stats_t stats;
    esp_err_t err = nvs_get_stats(NULL, &stats);
    if (err != ESP_OK) return err;
    if (stats.free_entries < (sizeof(*c) + 31) / 32 + 128) return ESP_ERR_NVS_NOT_ENOUGH_SPACE;
    nvs_handle_t handle;
    err = nvs_open("omk_netdiag", NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(handle, "snapshot1", c, sizeof(*c));
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    return err;
}

/* A bounded priority slot, not an unbounded event history. At equal severity an
 * unsaved record wins; a same-boot superset may refresh its timing. A lesser
 * anomaly never replaces a critical record, including one already in NVS. */
static bool save_needed(const nd_snapshot_t *s) {
    return (s->critical_reasons & ~saved_critical) ||
           (!s->critical_reasons && !saved_critical && saves < ND_EARLY_SAVE_LIMIT &&
            (s->reasons & ~saved_reasons));
}
static bool newer(uint32_t a, uint32_t b) { return (int32_t)(a - b) > 0; }
static bool prefer(const nd_snapshot_t *candidate, const nd_snapshot_t *held) {
    if (!candidate->reasons) return false;
    if (!held->reasons) return true;
    if (!!candidate->critical_reasons != !!held->critical_reasons)
        return candidate->critical_reasons != 0;
    if (candidate->boot_id == held->boot_id &&
        (candidate->reasons & held->reasons) == held->reasons &&
        (candidate->critical_reasons & held->critical_reasons) == held->critical_reasons)
        return newer(candidate->sequence, held->sequence);
    return !save_needed(held) && save_needed(candidate);
}
static void observe_once(void) {
    nd_snapshot_t sample;
    network_diag_snapshot(&sample);
    sample.sequence = ++sequence;
    sample.reasons = network_diag_reasons(&sample);
    sample.critical_reasons = network_diag_critical(&sample);
    uint64_t now = sample.sampled_us;
    /* Sole writer is this observer; readers take only a bounded copy lock. */
    bool replace = prefer(&sample, &retained);
    if (replace) {
        portENTER_CRITICAL(&view_lock);
        metadata(&sample);
        portEXIT_CRITICAL(&view_lock);
        seal(&rtc_faults[fault_slot], &sample);
        fault_slot ^= 1;
        portENTER_CRITICAL(&view_lock);
        retained = sample;
        portEXIT_CRITICAL(&view_lock);
    }
    /* Save the retained fault even when the live anomaly has already cleared. */
    if (!storage_disabled && saves < ND_SAVE_LIMIT && save_needed(&retained) &&
        (!saves || now - last_save_us >= ND_SAVE_GAP_US)) {
        checkpoint_t checkpoint;
        sample = retained;
        portENTER_CRITICAL(&view_lock);
        metadata(&sample);
        portEXIT_CRITICAL(&view_lock);
        sample.saved_reasons |= sample.reasons;
        sample.saved_critical |= sample.critical_reasons;
        sample.saves = saves + 1;
        seal(&checkpoint, &sample);
        uint64_t save_started = esp_timer_get_time();
        portENTER_CRITICAL(&view_lock);
        flash_started_us = save_started; flash_finished_us = 0;
        portEXIT_CRITICAL(&view_lock);
        esp_err_t err = save_checkpoint(&checkpoint);
        uint64_t save_finished = esp_timer_get_time();
        portENTER_CRITICAL(&view_lock);
        save_error = err;
        if (err == ESP_OK) {
            saved_reasons = sample.saved_reasons;
            saved_critical = sample.saved_critical;
            saves = sample.saves;
            saved = sample;
        } else storage_disabled = true;
        flash_finished_us = save_finished;
        portEXIT_CRITICAL(&view_lock);
        last_save_us = save_finished;
    }
    /* Metadata/progress checkpoint has its own two slots. Never clear or
     * overwrite fault slots here, including immediately after a soft reset. */
    network_diag_snapshot(&sample);
    sample.sequence = sequence;
    sample.reasons = network_diag_reasons(&sample);
    sample.critical_reasons = network_diag_critical(&sample);
    portENTER_CRITICAL(&view_lock);
    metadata(&sample);
    portEXIT_CRITICAL(&view_lock);
    seal(&rtc_checkpoints[sequence % 2], &sample);
}

static void observer_task(void *argument) {
    (void)argument;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        observe_once();
    }
}
void network_diag_start(void) {
    if (initialized) return;
    initialized = true;
    checkpoint_t disk = {0};
    nvs_handle_t handle;
    esp_err_t err = nvs_open("omk_netdiag", NVS_READONLY, &handle);
    if (err == ESP_OK) {
        size_t size = sizeof(disk);
        err = nvs_get_blob(handle, "snapshot1", &disk, &size);
        nvs_close(handle);
        if (err == ESP_OK && (size != sizeof(disk) || !valid(&disk))) err = ESP_ERR_INVALID_STATE;
    }
    if (err == ESP_OK) {
        saved = retained = disk.snapshot;
        saves = disk.snapshot.saves; saved_reasons = disk.snapshot.saved_reasons;
        saved_critical = disk.snapshot.saved_critical;
        sequence = disk.snapshot.sequence;
    } else if (err != ESP_ERR_NVS_NOT_FOUND) {
        storage_disabled = true; save_error = err;
    }
    esp_reset_reason_t reset = esp_reset_reason();
    if (reset != ESP_RST_POWERON && reset != ESP_RST_BROWNOUT) {
        /* First restore budgets, then select the fault. Progress must never
         * displace a fault simply because its generation is newer. */
        for (unsigned i = 0; i < 4; ++i) {
            checkpoint_t *r = i < 2 ? &rtc_checkpoints[i] : &rtc_faults[i-2];
            if (!valid(r)) continue;
            if (r->snapshot.save_error != ESP_OK) {
                storage_disabled = true; save_error = r->snapshot.save_error;
            }
            if (r->snapshot.saves > saves) saves = r->snapshot.saves;
            saved_reasons |= r->snapshot.saved_reasons;
            saved_critical |= r->snapshot.saved_critical;
            if (newer(r->snapshot.sequence, sequence)) sequence = r->snapshot.sequence;
        }
        for (unsigned i = 0; i < 2; ++i) {
            checkpoint_t *r = &rtc_faults[i];
            if (!valid(r) || !r->snapshot.reasons) continue;
            if (!retained.reasons ||
                (!!r->snapshot.critical_reasons > !!retained.critical_reasons) ||
                (!!r->snapshot.critical_reasons == !!retained.critical_reasons &&
                 newer(r->snapshot.sequence, retained.sequence))) retained = r->snapshot;
        }
        /* Alternate actual fault writes, independent of normal tick parity. */
        if (valid(&rtc_faults[0])) fault_slot = 1;
        if (valid(&rtc_faults[1]) && (!valid(&rtc_faults[0]) ||
            newer(rtc_faults[1].snapshot.sequence, rtc_faults[0].snapshot.sequence))) fault_slot = 0;
    } else {
        /* Only cold/brownout boot invalidates untrusted RTC. No soft-reset gap. */
        for (unsigned i = 0; i < 2; ++i) {
            rtc_checkpoints[i].magic = 0;
            rtc_faults[i].magic = 0;
        }
    }
    last_save_us = esp_timer_get_time();
    uint64_t boot;
    esp_fill_random(&boot, sizeof(boot));
    network_diag_init_core(boot ? boot : 1, esp_app_get_description()->app_elf_sha256);
    /* IDF FreeRTOS stack depth is in bytes, including xTaskCreateStatic. */
    if (!xTaskCreateStatic(observer_task, "net_diag", sizeof(observer_stack), NULL,
                          1, observer_stack, &observer_tcb)) {
        storage_disabled = true; save_error = ESP_ERR_NO_MEM;
    }
}

static bool append(char *out, size_t size, size_t *used, const char *fmt, ...) {
    if (*used >= size) return false;
    va_list args; va_start(args, fmt);
    int n = vsnprintf(out + *used, size - *used, fmt, args);
    va_end(args);
    if (n < 0 || (size_t)n >= size - *used) return false;
    *used += n;
    return true;
}
static void hex_sha(char out[65], const uint8_t sha[32]) {
    static const char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < 32; ++i) { out[2*i]=hex[sha[i]>>4]; out[2*i+1]=hex[sha[i]&15]; }
    out[64] = 0;
}
static bool format_report(const nd_snapshot_t *s, unsigned view, char *out, size_t size) {
    char sha[65]; hex_sha(sha, s->elf_sha);
    size_t used = 0;
    bool ok = append(out,size,&used,"{\"schema\":2,\"view\":%u,\"boot\":\"%016" PRIx64
        "\",\"seq\":%" PRIu32 ",\"at_us\":%" PRIu64 ",\"elf\":\"%s\",\"reasons\":%" PRIu32
        ",\"saved_reasons\":%" PRIu32 ",\"saves\":%" PRIu32 ",\"save_error\":%" PRId32
        ",\"dropped\":%" PRIu32 ",\"critical\":%" PRIu32 ",\"saved_critical\":%" PRIu32 ",\"flash_us\":[%" PRIu64 ",%" PRIu64 "],\"stats\":[",
        view,s->boot_id,s->sequence,s->sampled_us,sha,s->reasons,s->saved_reasons,s->saves,s->save_error,
        s->dropped,s->critical_reasons,s->saved_critical,s->flash_started_us,s->flash_finished_us);
    for (unsigned i=0; ok && i<ND_OPERATIONS; ++i) {
        const nd_stats_t *a=&s->stats[i];
        ok=append(out,size,&used,"%s[%u,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRId32
            ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 "]",
            i?",":"",i,a->calls,a->completed,a->errors,a->last_rc,a->last_ms,a->max_ms,
            a->last_wait_ms,a->max_wait_ms,a->last_locked_ms,a->last_end_us);
    }
    ok=ok && append(out,size,&used,"],\"active\":[");
    bool comma=false;
    for (unsigned i=0; ok && i<ND_ACTIVE_SLOTS; ++i) {
        const nd_active_t *a=&s->active[i];
        if (!a->token) continue;
        ok=append(out,size,&used,"%s[%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]",comma?",":"",a->token,a->task,
            a->op,a->phase,a->started_us,a->wait_us,a->acquired_us);
        comma=true;
    }
    ok=ok && append(out,size,&used,"],\"mesh_errors\":[");
    for (unsigned i=0; ok && i<8; ++i)
        ok=append(out,size,&used,"%s[%" PRId32 ",%" PRIu32 "]",i?",":"",s->mesh_errors[i].rc,s->mesh_errors[i].count);
    const nd_progress_t *p=&s->progress;
    return ok && append(out,size,&used,"],\"mesh_error_other\":%" PRIu32 ",\"progress\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
        ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
        ",%" PRIu64 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]}",s->mesh_error_other,p->ticks,
        p->posted,p->post_errors,p->entered,p->exited,p->tick_us,p->post_us,p->enter_us,p->exit_us,
        p->recovery_us,p->recovery_flags,p->recovery_stage,p->recovery_action);
}
bool network_diag_report(char *out, size_t size) {
    /* Sole consumer: existing sen66_tx, including relay-only nodes. The
     * observer NEVER waits for this task, MQTT, or any transport ACK. */
    uint64_t now=esp_timer_get_time();
    if (!initialized || now < next_report_us) return false;
    nd_snapshot_t s;
    unsigned view=next_report_view;
    /* Missing history uses this same slot for live statistics, never an extra
     * message. Keep retained/saved records untouched, including across boots. */
    if (!get_view(view,&s) && !get_view(view=0,&s)) return false;
    /* A live seq identifies an MQTT sample, not delivery success. Retries take
     * a fresh sample; gaps are allowed. Compare (node,view,boot,seq,at_us), not
     * seq alone. USB and persisted snapshot generations remain unchanged. */
    if (view==0) s.sequence=++live_report_sequence;
    next_report_us=now+ND_REPORT_GAP_US;
    return format_report(&s,view,out,size);
}

void network_diag_report_result(bool enqueued) {
    /* Sender-only state; never acknowledges Gateway persistence or clears RAM. */
    next_report_us = esp_timer_get_time() + (enqueued ? ND_REPORT_GAP_US : 30000000ULL);
    if (enqueued) next_report_view = (next_report_view + 1U) % 3U;
}

bool network_diag_usb(const char *line, uint64_t node_id) {
    if (strncmp(line,"OMKDIAG ",8)!=0) return false;
    /* Strict fixed grammar: OMKDIAG <view 0..2> <page 0..26>. */
    unsigned view=0, page=0;
    const char *p=line+8;
    if (*p<'0'||*p>'2'||p[1]!=' ') return true;
    view=(unsigned)(*p-'0'); p+=2;
    if (*p<'0'||*p>'9') return true;
    while (*p>='0'&&*p<='9') { page=page*10+(unsigned)(*p++-'0'); if(page>26) return true; }
    if (*p) return true;
    nd_snapshot_t s;
    if (!get_view(view,&s)) memset(&s,0,sizeof(s));
    char response[240]; size_t used=0;
    bool ok=append(response,sizeof(response),&used,"{\"nd\":1,\"node\":\"%012" PRIx64
        "\",\"v\":%u,\"p\":%u,",node_id,view,page);
    if (page==0) {
        char sha[65]; hex_sha(sha,s.elf_sha);
        ok=ok && append(response,sizeof(response),&used,"\"boot\":\"%016" PRIx64
            "\",\"seq\":%" PRIu32 ",\"at\":%" PRIu64 ",\"elf\":\"%s\",\"critical\":%" PRIu32 ",\"saved_critical\":%" PRIu32,s.boot_id,s.sequence,s.sampled_us,sha,s.critical_reasons,s.saved_critical);
    } else if (page<=8) {
        const nd_stats_t *a=&s.stats[page-1];
        ok=ok && append(response,sizeof(response),&used,"\"s\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRId32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRIu64 "]",a->calls,a->completed,a->errors,a->last_rc,a->last_ms,a->max_ms,
            a->last_wait_ms,a->max_wait_ms,a->last_locked_ms,a->last_end_us);
    } else if (page<=16) {
        const nd_active_t *a=&s.active[page-9];
        ok=ok && append(response,sizeof(response),&used,"\"a\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]",a->token,a->task,a->op,a->phase,
            a->started_us,a->wait_us,a->acquired_us);
    } else if (page==17) {
        const nd_progress_t *a=&s.progress;
        ok=ok && append(response,sizeof(response),&used,"\"e\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]",
            a->ticks,a->posted,a->post_errors,a->entered,a->exited,a->tick_us,a->enter_us,a->exit_us,a->recovery_us);
    } else if (page==18) {
        uint64_t begin,end;
        portENTER_CRITICAL(&view_lock);
        begin=flash_started_us; end=flash_finished_us;
        portEXIT_CRITICAL(&view_lock);
        ok=ok && append(response,sizeof(response),&used,"\"r\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRId32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu64 ",%" PRIu64 "]",s.reasons,
            s.saved_reasons,s.saves,s.save_error,s.dropped,s.usb_dropped,begin,end);
    } else {
        const nd_mesh_error_t *e=&s.mesh_errors[page-19];
        ok=ok && append(response,sizeof(response),&used,"\"mesh_error\":[%" PRId32 ",%" PRIu32
            "],\"other\":%" PRIu32,e->rc,e->count,s.mesh_error_other);
    }
    ok=ok && append(response,sizeof(response),&used,"}\n");
    if (!ok || usb_serial_jtag_write_bytes(response,used,0)!=(int)used) {
        portENTER_CRITICAL(&view_lock); ++usb_dropped; portEXIT_CRITICAL(&view_lock);
    }
    return true;
}
#endif
