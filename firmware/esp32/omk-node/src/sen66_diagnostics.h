#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* One physical SEN66 endpoint today. State survives sensor recovery, not reboot.
 * Read counters belong to the sensor task; publish/enqueue counters to the
 * sender. All writers and snapshots use the same short critical section. */
typedef struct {
    uint64_t boot_id;
    uint32_t read_attempts, read_success, not_ready, read_errors;
    uint32_t publish_attempts, publish_failures, publish_unavailable;
    uint32_t enqueue_success, enqueue_failures;
    uint32_t last_enqueue_seq, last_failed_seq;
    esp_err_t last_read_error;
    uint64_t last_read_ms, last_interval_ms, max_interval_ms, max_read_ms, max_publish_ms;
} sen66_diagnostics_t;

void sen66_diagnostics_read_begin(uint64_t now_ms);
uint32_t sen66_diagnostics_read_end(esp_err_t error, bool ready, uint64_t duration_ms);
void sen66_diagnostics_publish_begin(void);
void sen66_diagnostics_publish_end(esp_err_t error, uint32_t seq, uint64_t duration_ms);
void sen66_diagnostics_enqueue_result(bool success, uint32_t seq);
sen66_diagnostics_t sen66_diagnostics_snapshot(void);
bool sen66_diagnostics_format(char *buffer, size_t size);
