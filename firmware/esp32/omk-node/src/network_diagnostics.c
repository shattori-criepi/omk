#include "network_diagnostics.h"
#ifdef OMK_NETWORK_DIAGNOSTICS
#include <limits.h>
#include <string.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
static nd_snapshot_t state;
static nd_rejections_t rejections;
static uint32_t next_token;
static void increment(uint32_t *v) { if (*v != UINT32_MAX) ++*v; }
void network_diag_rejected(unsigned reason) {
    if (reason >= ND_REJECTIONS) return;
    portENTER_CRITICAL(&lock);
    increment(&rejections.count[reason]);
    portEXIT_CRITICAL(&lock);
}
nd_rejections_t network_diag_rejections(void) {
    portENTER_CRITICAL(&lock);
    nd_rejections_t result = rejections;
    portEXIT_CRITICAL(&lock);
    return result;
}
static uint32_t milliseconds(uint64_t us) {
    return us / 1000 > UINT32_MAX ? UINT32_MAX : (uint32_t)(us / 1000);
}
void network_diag_init_core(uint64_t boot_id, const uint8_t elf_sha[32]) {
    /* Startup, before networking or the observer task. */
    memset(&state, 0, sizeof(state));
    memset(&rejections, 0, sizeof(rejections));
    state.schema = ND_SCHEMA;
    state.boot_id = boot_id;
    memcpy(state.elf_sha, elf_sha, sizeof(state.elf_sha));
}
uint32_t network_diag_begin(unsigned op) {
    if (op >= ND_OPERATIONS) return 0;
    uint64_t now = esp_timer_get_time();
    uint32_t task = (uint32_t)(uintptr_t)xTaskGetCurrentTaskHandle();
    uint32_t token = 0;
    portENTER_CRITICAL(&lock);
    increment(&state.stats[op].calls);
    for (unsigned i = 0; i < ND_ACTIVE_SLOTS; ++i) {
        if (state.active[i].token) continue;
        /* Skip zero; no pointer into the caller's stack is retained. */
        bool collision;
        do {
            do { ++next_token; } while (!next_token);
            collision = false;
            for (unsigned j=0; j<ND_ACTIVE_SLOTS; ++j)
                if (state.active[j].token == next_token) collision = true;
        } while (collision); /* At most eight occupied IDs, including wrap. */
        token = next_token;
        state.active[i] = (nd_active_t){.token=token, .task=task, .op=op,
                                      .phase=ND_ENTER, .started_us=now};
        break;
    }
    if (!token) increment(&state.dropped);
    portEXIT_CRITICAL(&lock);
    return token;
}
static void phase(uint32_t token, unsigned p) {
    if (!token) return;
    uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    for (unsigned i = 0; i < ND_ACTIVE_SLOTS; ++i) {
        nd_active_t *a = &state.active[i];
        if (a->token != token) continue;
        a->phase = p;
        if (p == ND_WAIT_LOCK) a->wait_us = now;
        else a->acquired_us = now;
        break;
    }
    portEXIT_CRITICAL(&lock);
}
void network_diag_wait(uint32_t token) { phase(token, ND_WAIT_LOCK); }
void network_diag_locked(uint32_t token) { phase(token, ND_LOCKED); }
int network_diag_end(uint32_t token, int rc, bool failed) {
    if (!token) return rc;
    uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    for (unsigned i = 0; i < ND_ACTIVE_SLOTS; ++i) {
        nd_active_t *a = &state.active[i];
        if (a->token != token) continue;
        nd_stats_t *s = &state.stats[a->op];
        uint32_t duration = milliseconds(now - a->started_us);
        uint32_t wait = a->acquired_us ? milliseconds(a->acquired_us - a->wait_us) : 0;
        increment(&s->completed);
        if (failed) increment(&s->errors);
        if (failed && a->op <= ND_MESH_BROADCAST) {
            unsigned e;
            for (e=0; e<8; ++e) {
                if (state.mesh_errors[e].count && state.mesh_errors[e].rc != rc) continue;
                state.mesh_errors[e].rc = rc;
                increment(&state.mesh_errors[e].count);
                break;
            }
            if (e==8) increment(&state.mesh_error_other);
        }
        s->last_rc = rc; s->last_ms = duration; s->last_end_us = now;
        s->last_wait_ms = wait;
        s->last_locked_ms = a->acquired_us ? milliseconds(now - a->acquired_us) : 0;
        if (duration > s->max_ms) s->max_ms = duration;
        if (wait > s->max_wait_ms) s->max_wait_ms = wait;
        if (duration >= 100) increment(&s->over_100ms);
        if (duration >= 1000) increment(&s->over_1s);
        if (duration >= 10000) increment(&s->over_10s);
        a->token = 0;
        break;
    }
    portEXIT_CRITICAL(&lock);
    return rc;
}
void network_diag_tick(uint64_t at_us) {
    portENTER_CRITICAL(&lock);
    increment(&state.progress.ticks); state.progress.tick_us = at_us;
    portEXIT_CRITICAL(&lock);
}
void network_diag_post(uint64_t at_us, int rc) {
    portENTER_CRITICAL(&lock);
    if (rc) increment(&state.progress.post_errors);
    else { increment(&state.progress.posted); state.progress.post_us = at_us; }
    portEXIT_CRITICAL(&lock);
}
void network_diag_control(bool entering) {
    uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    if (entering) {
        increment(&state.progress.entered); state.progress.enter_us = now;
    } else { increment(&state.progress.exited); state.progress.exit_us = now; }
    portEXIT_CRITICAL(&lock);
}
void network_diag_recovery(uint32_t flags, uint32_t stage, uint32_t action) {
    uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    state.progress.recovery_us = now;
    state.progress.recovery_flags = flags;
    state.progress.recovery_stage = stage;
    state.progress.recovery_action = action;
    portEXIT_CRITICAL(&lock);
}
void network_diag_snapshot(nd_snapshot_t *out) {
    /* One bounded <1KiB copy; formatting, CRC and I/O are always outside. */
    portENTER_CRITICAL(&lock);
    *out = state;
    portEXIT_CRITICAL(&lock);
    out->sampled_us = esp_timer_get_time();
}
uint32_t network_diag_reasons(const nd_snapshot_t *s) {
    uint32_t reasons = 0;
    for (unsigned op = 0; op < ND_OPERATIONS; ++op)
        if (s->stats[op].max_ms > 10000) reasons |= 1U << op;
    for (unsigned i = 0; i < ND_ACTIVE_SLOTS; ++i) {
        const nd_active_t *a = &s->active[i];
        if (a->token && s->sampled_us > a->started_us + 10000000ULL)
            reasons |= 1U << a->op;
    }
    /* Compare last progress, not last post (which keeps advancing in a stall). */
    const nd_progress_t *p = &s->progress;
    if (p->ticks && s->sampled_us > p->tick_us + 90000000ULL) reasons |= 1U << 8;
    if (p->ticks && s->sampled_us > 90000000ULL &&
        s->sampled_us > p->exit_us + 90000000ULL) reasons |= 1U << 9;
    if (p->ticks && s->sampled_us > 90000000ULL &&
        s->sampled_us > p->recovery_us + 90000000ULL) reasons |= 1U << 10;
    return reasons;
}
uint32_t network_diag_critical(const nd_snapshot_t *s) {
    uint32_t reasons = network_diag_reasons(s) & (7U << 8);
    for (unsigned op = 0; op < ND_OPERATIONS; ++op)
        if (s->stats[op].max_ms >= 30000) reasons |= 1U << op;
    for (unsigned i = 0; i < ND_ACTIVE_SLOTS; ++i) {
        const nd_active_t *a = &s->active[i];
        if (a->token && s->sampled_us >= a->started_us + 30000000ULL)
            reasons |= 1U << a->op;
    }
    return reasons;
}
#endif
