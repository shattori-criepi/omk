#include "sen66_delivery.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mqtt_registration.h"
#include "node_registration.h"

#define SAMPLE_MAX_AGE_MS 30000U
typedef struct {
    sen66_measurement_t sample;
    uint64_t measured_ms;
    uint32_t generation;
    char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
} pending_sample_t;
_Static_assert(sizeof(pending_sample_t) <= 128, "Keep the mailbox bounded");

static portMUX_TYPE delivery_lock = portMUX_INITIALIZER_UNLOCKED;
static TaskHandle_t sender;
static pending_sample_t pending;
static bool occupied;
static uint32_t generation, submitted, replaced, invalidated, expired, unavailable;
static sen66_tx_phase_t phase;
static uint32_t active_seq;
static uint64_t phase_since_ms, max_enqueue_ms, max_outbox_ms;
static bool status_sending;
static uint64_t status_since_ms, max_status_ms;

static uint64_t now_ms(void) { return (uint64_t)(esp_timer_get_time() / 1000); }

void sen66_delivery_wake(void) {
    portENTER_CRITICAL(&delivery_lock);
    TaskHandle_t task = sender;
    portEXIT_CRITICAL(&delivery_lock);
    if (task) xTaskNotifyGive(task);
}

void sen66_delivery_invalidate(void) {
    portENTER_CRITICAL(&delivery_lock);
    ++generation;
    if (occupied) { ++invalidated; occupied = false; }
    portEXIT_CRITICAL(&delivery_lock);
    sen66_delivery_wake();
}

esp_err_t sen66_delivery_submit(const char *logical_id, const sen66_measurement_t *sample) {
    if (!logical_id || !sample || !logical_id[0] ||
        strlen(logical_id) > OMK_NODE_LOGICAL_ID_MAX_LENGTH) return ESP_ERR_INVALID_ARG;
    uint64_t time = now_ms();
    portENTER_CRITICAL(&delivery_lock);
    if (!sender) {
        ++unavailable;
        portEXIT_CRITICAL(&delivery_lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (occupied) ++replaced;
    pending.sample = *sample;
    /* The driver submits the current read in this boot. Reconstruct its full
     * monotonic time from the wire-format uint32, including a 32-bit rollover.
     * Time spent getting the Logical ID must count toward the freshness limit. */
    pending.measured_ms = time - (uint32_t)((uint32_t)time - sample->uptime_ms);
    pending.generation = generation;
    strcpy(pending.logical_id, logical_id);
    occupied = true;
    ++submitted;
    portEXIT_CRITICAL(&delivery_lock);
    sen66_delivery_wake();
    return ESP_OK;
}

void sen66_delivery_phase(sen66_tx_phase_t next, uint32_t seq) {
    uint64_t time = now_ms();
    portENTER_CRITICAL(&delivery_lock);
    uint64_t elapsed = time - phase_since_ms;
    if (phase == SEN66_TX_ENQUEUE && elapsed > max_enqueue_ms) max_enqueue_ms = elapsed;
    if (phase == SEN66_TX_OUTBOX && elapsed > max_outbox_ms) max_outbox_ms = elapsed;
    phase = next;
    active_seq = seq;
    phase_since_ms = time;
    portEXIT_CRITICAL(&delivery_lock);
}

void sen66_delivery_status_sending(bool sending) {
    uint64_t time = now_ms();
    portENTER_CRITICAL(&delivery_lock);
    if (sending) status_since_ms = time;
    else if (status_sending && time - status_since_ms > max_status_ms)
        max_status_ms = time - status_since_ms;
    status_sending = sending;
    portEXIT_CRITICAL(&delivery_lock);
}

/* One iteration is also exercised by host tests with a blocked MQTT stub. */
static void send_latest(void) {
    if (!mqtt_registration_is_connected()) return;
    pending_sample_t item;
    portENTER_CRITICAL(&delivery_lock);
    bool have_sample = occupied;
    if (have_sample) { item = pending; occupied = false; }
    portEXIT_CRITICAL(&delivery_lock);
    if (!have_sample) return;

    sen66_delivery_phase(SEN66_TX_IDENTITY, item.sample.sequence);
    char current_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
    esp_err_t err = node_registration_get_logical_id(current_id, sizeof(current_id));
    uint64_t time = now_ms();
    portENTER_CRITICAL(&delivery_lock);
    bool obsolete = item.generation != generation || err != ESP_OK ||
                    strcmp(current_id, item.logical_id) != 0;
    bool stale = time - item.measured_ms > SAMPLE_MAX_AGE_MS;
    if (obsolete) ++invalidated;
    else if (stale) ++expired;
    portEXIT_CRITICAL(&delivery_lock);
    if (!obsolete && !stale) {
        /* Keep original identity/time/sequence across nonblocking admission.
         * A refused value may return to the same mailbox only if no newer
         * sample or registration change superseded it. */
        esp_err_t result = mqtt_registration_publish_sen66(item.logical_id, &item.sample);
        if (result == ESP_ERR_NO_MEM) {
            portENTER_CRITICAL(&delivery_lock);
            if (!occupied && item.generation == generation) { pending = item; occupied = true; }
            portEXIT_CRITICAL(&delivery_lock);
        }
    }
    sen66_delivery_phase(SEN66_TX_IDLE, 0);
}

static void sender_task(void *argument) {
    (void)argument;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
        mqtt_registration_flush_sensor_status();
        send_latest();
    }
}

esp_err_t sen66_delivery_start(void) {
    if (sender) return ESP_OK; /* Only called by startup. Never destroy/recreate. */
    TaskHandle_t task = NULL;
    if (xTaskCreate(sender_task, "sen66_tx", 4096, NULL, 5, &task) != pdPASS)
        return ESP_ERR_NO_MEM;
    portENTER_CRITICAL(&delivery_lock);
    sender = task;
    portEXIT_CRITICAL(&delivery_lock);
    return ESP_OK;
}

bool sen66_delivery_format(char *buffer, size_t size) {
    uint64_t time = now_ms();
    portENTER_CRITICAL(&delivery_lock);
    uint32_t s = submitted, r = replaced, i = invalidated, e = expired, u = unavailable;
    unsigned p = phase;
    uint32_t seq = active_seq;
    bool waiting = occupied;
    uint64_t age = time >= phase_since_ms ? time - phase_since_ms : 0;
    uint64_t enqueue = max_enqueue_ms, outbox = max_outbox_ms;
    uint64_t status_age = status_sending && time >= status_since_ms ? time - status_since_ms : 0;
    uint64_t status_max = max_status_ms;
    portEXIT_CRITICAL(&delivery_lock);
    int n = snprintf(buffer, size,
        "{\"submitted\":%" PRIu32 ",\"replaced\":%" PRIu32
        ",\"invalidated\":%" PRIu32 ",\"expired\":%" PRIu32
        ",\"unavailable\":%" PRIu32 ",\"pending\":%s,\"phase\":%u"
        ",\"active_seq\":%" PRIu32 ",\"phase_age_ms\":%" PRIu64
        ",\"max_enqueue_ms\":%" PRIu64 ",\"max_outbox_ms\":%" PRIu64
        ",\"status_age_ms\":%" PRIu64 ",\"max_status_ms\":%" PRIu64 "}",
        s, r, i, e, u, waiting ? "true" : "false", p, seq, p ? age : 0, enqueue, outbox,
        status_age, status_max);
    return n >= 0 && (size_t)n < size;
}
