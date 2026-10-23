#include "diagnostic_runtime.h"
#include <inttypes.h>
#include <stdio.h>
#include "esp_system.h"
#include "esp_timer.h"
#include "mqtt_registration.h"
#include "sen66_delivery.h"
#include "sen66_diagnostics.h"

bool diagnostic_runtime_page(unsigned page, char *out, size_t size) {
    uint64_t at = esp_timer_get_time();
    int n = -1;
    if (page == 27) {
        sen66_diagnostics_t d = sen66_diagnostics_snapshot();
        uint32_t rc, to;
        mqtt_registration_snapshot_sensor(&rc, &to);
        n = snprintf(out, size,
            "\"at\":%" PRIu64 ",\"sensor_boot\":\"%016" PRIx64
            "\",\"read\":[%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
            ",%" PRIu64 ",%" PRIu32 ",%" PRIu32 "]",
            at,d.boot_id,d.read_attempts,d.read_success,d.read_errors,d.not_ready,d.last_read_ms,rc,to);
    } else if (page == 28) {
        sen66_delivery_snapshot_t d = sen66_delivery_snapshot();
        n = snprintf(out, size,
            "\"at\":%" PRIu64 ",\"mailbox\":[%" PRIu32 ",%" PRIu32
            ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%u,%" PRIu32
            ",%" PRIu32 ",%" PRIu64 "]",
            at,d.submitted,d.replaced,d.expired,d.invalidated,d.unavailable,
            d.pending ? 1U : 0U,d.phase,d.active_seq,d.phase_age_ms);
    } else if (page == 29) {
        omk_mqtt_progress_t p = mqtt_registration_snapshot_progress();
        /* An ACK on the other core must not appear newer than this clock. */
        at = esp_timer_get_time();
        n = snprintf(out, size,
            "\"at\":%" PRIu64 ",\"probe\":[%" PRIu32 ",%" PRIu32 ",%d,%u,%u]",
            at,p.ack_count,p.ack_ms,p.pending_id,p.submitting ? 1U : 0U,
            mqtt_progress_is_fresh(&p,(uint32_t)(at/1000)) ? 1U : 0U);
    } else if (page == 30) {
        n = snprintf(out, size,"\"at\":%" PRIu64 ",\"heap\":[%u,%u]",
            at,(unsigned)esp_get_free_heap_size(),(unsigned)esp_get_minimum_free_heap_size());
    }
    return n >= 0 && (size_t)n < size;
}
