#include "sen66_diagnostics.h"

#include <inttypes.h>
#include <stdio.h>
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
static sen66_diagnostics_t counters;

void sen66_diagnostics_read_begin(uint64_t now_ms) {
    /* Initialized by the sole writer, outside the critical section. */
    uint64_t boot_id = counters.boot_id;
    if (!boot_id) {
        esp_fill_random(&boot_id, sizeof(boot_id));
        if (!boot_id) boot_id = 1;
    }
    portENTER_CRITICAL(&lock);
    counters.boot_id = boot_id;
    if (counters.read_attempts) {
        counters.last_interval_ms = now_ms - counters.last_read_ms;
        if (counters.last_interval_ms > counters.max_interval_ms)
            counters.max_interval_ms = counters.last_interval_ms;
    }
    counters.last_read_ms = now_ms;
    ++counters.read_attempts;
    portEXIT_CRITICAL(&lock);
}

uint32_t sen66_diagnostics_read_end(esp_err_t error, bool ready, uint64_t duration_ms) {
    portENTER_CRITICAL(&lock);
    if (error != ESP_OK) {
        ++counters.read_errors;
        counters.last_read_error = error;
    } else if (ready) {
        ++counters.read_success;
    } else {
        ++counters.not_ready;
    }
    if (duration_ms > counters.max_read_ms) counters.max_read_ms = duration_ms;
    uint32_t seq = counters.read_success;
    portEXIT_CRITICAL(&lock);
    return seq;
}

void sen66_diagnostics_publish_begin(void) {
    portENTER_CRITICAL(&lock);
    ++counters.publish_attempts;
    portEXIT_CRITICAL(&lock);
}

void sen66_diagnostics_publish_end(esp_err_t error, uint32_t seq, uint64_t duration_ms) {
    portENTER_CRITICAL(&lock);
    if (error != ESP_OK) {
        ++counters.publish_failures;
        counters.last_failed_seq = seq;
        if (error == ESP_ERR_INVALID_STATE) ++counters.publish_unavailable;
    }
    if (duration_ms > counters.max_publish_ms) counters.max_publish_ms = duration_ms;
    portEXIT_CRITICAL(&lock);
}

void sen66_diagnostics_enqueue_result(bool success, uint32_t seq) {
    portENTER_CRITICAL(&lock);
    if (success) {
        ++counters.enqueue_success;
        counters.last_enqueue_seq = seq;
    } else {
        ++counters.enqueue_failures;
    }
    portEXIT_CRITICAL(&lock);
}

sen66_diagnostics_t sen66_diagnostics_snapshot(void) {
    portENTER_CRITICAL(&lock);
    sen66_diagnostics_t snapshot = counters;
    portEXIT_CRITICAL(&lock);
    return snapshot;
}

bool sen66_diagnostics_format(char *buffer, size_t size) {
    sen66_diagnostics_t d = sen66_diagnostics_snapshot();
    if (!d.boot_id) return false; /* No read attempt on this Node yet. */
    int n = snprintf(buffer, size,
        "{\"boot_id\":\"%016" PRIx64 "\",\"snapshot_ms\":%" PRIu64
        ",\"read_attempts\":%" PRIu32 ",\"read_success\":%" PRIu32
        ",\"not_ready\":%" PRIu32 ",\"read_errors\":%" PRIu32
        ",\"publish_attempts\":%" PRIu32 ",\"publish_failures\":%" PRIu32
        ",\"publish_unavailable\":%" PRIu32
        ",\"enqueue_success\":%" PRIu32 ",\"enqueue_failures\":%" PRIu32
        ",\"last_enqueue_seq\":%" PRIu32 ",\"last_failed_seq\":%" PRIu32
        ",\"last_read_error\":%d,\"last_read_ms\":%" PRIu64
        ",\"last_interval_ms\":%" PRIu64 ",\"max_interval_ms\":%" PRIu64
        ",\"max_read_ms\":%" PRIu64 ",\"max_publish_ms\":%" PRIu64 "}",
        d.boot_id, (uint64_t)(esp_timer_get_time() / 1000),
        d.read_attempts, d.read_success, d.not_ready, d.read_errors,
        d.publish_attempts, d.publish_failures, d.publish_unavailable,
        d.enqueue_success, d.enqueue_failures, d.last_enqueue_seq, d.last_failed_seq,
        (int)d.last_read_error, d.last_read_ms, d.last_interval_ms, d.max_interval_ms,
        d.max_read_ms, d.max_publish_ms);
    return n >= 0 && (size_t)n < size;
}
