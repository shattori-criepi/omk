#pragma once

#include "sen66_sensor.h"

/* One copied pending sample, plus at most one in the sender. No network calls
 * or allocation in submit/invalidate. A blocked sender never owns this lock. */
esp_err_t sen66_delivery_start(void);
esp_err_t sen66_delivery_submit(const char *logical_id, const sen66_measurement_t *sample);
void sen66_delivery_invalidate(void);
void sen66_delivery_wake(void);

typedef enum {
    SEN66_TX_IDLE, SEN66_TX_IDENTITY, SEN66_TX_ENQUEUE, SEN66_TX_OUTBOX,
} sen66_tx_phase_t;
void sen66_delivery_phase(sen66_tx_phase_t phase, uint32_t seq);
/* Separate duration for registration status, including callback-only fallback. */
void sen66_delivery_status_sending(bool sending);
bool sen66_delivery_format(char *buffer, size_t size);
