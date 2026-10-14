#pragma once
#include "mqtt_client.h"
/* Diagnostic path only. -3 means API mutex busy; caller retains the record.
 * No transport I/O; copies one bounded message into the existing outbox. */
int esp_mqtt_client_enqueue_diagnostic(esp_mqtt_client_handle_t client,
                                       const char *topic, const char *data);

/* -3: mutex busy, -2: capacity exhausted. No socket I/O, no mutex wait.
 * submitted runs with the recursive MQTT mutex held, before any ACK can arrive.
 * It must only update RAM and must not call MQTT. It is omitted on busy/invalid arguments; callers also finish failed
 * submissions (idempotently) to release their in-progress flag. */
int esp_mqtt_client_enqueue_bounded(esp_mqtt_client_handle_t client,
    const char *topic, const char *data, int len, int qos, int retain,
    unsigned limit, void (*submitted)(int));
int esp_mqtt_client_get_outbox_size_nowait(esp_mqtt_client_handle_t client);

/* Registration-only synchronous publish; header/topic-inclusive capacity is
 * checked while holding the existing MQTT mutex through the publish call. */
int esp_mqtt_client_publish_bounded(esp_mqtt_client_handle_t client,
    const char *topic, const char *data, int len, int qos, int retain, unsigned limit);
