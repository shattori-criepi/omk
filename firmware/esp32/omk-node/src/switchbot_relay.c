#include "switchbot_relay.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "node_messages.h"
#include "node_identity.h"

static const char *TAG = "switchbot_relay";
#define SWITCHBOT_RELAY_INTERVAL_US (25LL * 1000LL * 1000LL)
#define SWITCHBOT_STATE_INTERVAL_US (10LL * 1000LL * 1000LL)
#define SWITCHBOT_RELAY_RETRY_US (1LL * 1000LL * 1000LL)
#define SWITCHBOT_FRAGMENT_JOIN_WINDOW_US (2LL * 1000LL * 1000LL)
#define SWITCHBOT_RELAY_DEVICE_SLOTS 16
#define SWITCHBOT_RELAY_SLOT_TTL_US (60LL * 1000LL * 1000LL)
#define SWITCHBOT_PENDING_TTL_US (30LL * 1000LL * 1000LL)
#define SWITCHBOT_PENDING_STATES 2

typedef struct {
    uint8_t manufacturer_data[31];
    uint8_t service_data[31];
    uint8_t manufacturer_length;
    uint8_t service_length;
    uint8_t state_kind; /* scheduling hint only: 0 unknown, 1 motion, 2 contact */
    uint8_t state;
    int rssi;
    int64_t observed_us;
} relay_sample_t;
typedef struct {
    bool in_use;
    uint8_t address[6];
    relay_sample_t latest;
    int64_t manufacturer_us;
    int64_t service_us;
    int64_t last_fragment_us;
    bool has_published;
    bool published_has_manufacturer;
    bool published_has_service;
    uint8_t published_state_kind;
    uint8_t published_state;
    int64_t last_publish_us;
    int64_t retry_after_us;
    relay_sample_t pending[SWITCHBOT_PENDING_STATES];
    uint8_t pending_count;
    bool overflow_logged;
} relay_device_slot_t;
static relay_device_slot_t relay_device_slots[SWITCHBOT_RELAY_DEVICE_SLOTS];

static int contact_state(uint8_t hal) {
    return hal == 0 ? 0 : (hal == 1 || hal == 2 ? 1 : -1);
}
/* These are priority hints, not a second public device decoder. Raw bytes
 * remain unchanged; the Gateway validates the actual model/schema. Field
 * mappings mirror omk_ble/switchbot.py. Counters/RSSI never trigger events. */
static void state_hint(relay_sample_t *sample) {
    sample->state_kind = 0;
    const uint8_t *service = sample->service_data;
    const uint8_t *manufacturer = sample->manufacturer_data;
    size_t sl = sample->service_length, ml = sample->manufacturer_length;
    int state = -1;
    if (sl) {
        if (sl < 3 || (service[2] & 0x7f) > 100) return;
        uint8_t type = service[0] & 0x7f;
        if (type == 0x64) {
            /* Contact encrypted service data is intentionally not decoded. */
            if (sl != 9 || service[0] != 0x64) return;
            state = contact_state((service[3] >> 1) & 3);
            if (state >= 0) sample->state_kind = 2;
        } else if (type == 0x73) {
            if (sl != 6 || (service[1] & 0x3f) || (service[5] & 0x40) ||
                (service[5] & 0x0c) == 0x0c ||
                ((service[5] & 3) != 1 && (service[5] & 3) != 2)) return;
            state = !!(service[1] & 0x40);
            sample->state_kind = 1;
        } else if ((type == 0x70 || (service[0] == 0x00 && service[1] == 0x20)) &&
                   sl == 7 && ml == 12 && !(manufacturer[7] & ~0xcc)) {
            /* Also prioritize the Gateway's supported, manually confirmed
             * 00/20 Presence layout. This hint never assigns a device model. */
            state = !!(manufacturer[7] & 0x40);
            sample->state_kind = 1;
        }
        /* An explicit non-state/unknown model must not fall through into a
         * coincidentally matching manufacturer payload shape. */
    } else if (ml == 10 && (manufacturer[7] & 0x3f) == 0x2c) {
        state = !!(manufacturer[7] & 0x40);
        sample->state_kind = 1;
    } else if (ml == 13 && (manufacturer[7] & 0x0f) == 0x0c) {
        state = contact_state((manufacturer[7] >> 4) & 3);
        if (state >= 0) sample->state_kind = 2;
    }
    /* Match Gateway preference: known manufacturer Motion/Contact snapshots
     * can be newer than the service snapshot included by active scanning. */
    if (sample->state_kind == 1 && ml == 10 && (manufacturer[7] & 0x3f) == 0x2c)
        state = !!(manufacturer[7] & 0x40);
    if (sample->state_kind == 2 && ml == 13 && (manufacturer[7] & 0x0f) == 0x0c) {
        int manufacturer_state = contact_state((manufacturer[7] >> 4) & 3);
        if (manufacturer_state >= 0) state = manufacturer_state;
    }
    if (sample->state_kind) sample->state = (uint8_t)state;
}
static void initialize_slot(relay_device_slot_t *slot, const uint8_t address[6]) {
    memset(slot, 0, sizeof(*slot));
    slot->in_use = true;
    memcpy(slot->address, address, 6);
}
static relay_device_slot_t *find_slot(const uint8_t address[6], int64_t now_us) {
    relay_device_slot_t *available = NULL, *oldest_expired = NULL;
    for (size_t i = 0; i < SWITCHBOT_RELAY_DEVICE_SLOTS; ++i) {
        relay_device_slot_t *slot = &relay_device_slots[i];
        if (slot->in_use && memcmp(slot->address, address, 6) == 0) return slot;
        if (!slot->in_use && available == NULL) available = slot;
        if (slot->in_use && now_us - slot->last_fragment_us >= SWITCHBOT_RELAY_SLOT_TTL_US &&
            (oldest_expired == NULL || slot->last_fragment_us < oldest_expired->last_fragment_us))
            oldest_expired = slot;
    }
    if (available == NULL) available = oldest_expired;
    if (available != NULL) initialize_slot(available, address);
    return available;
}
static void pop_pending(relay_device_slot_t *slot) {
    if (--slot->pending_count) slot->pending[0] = slot->pending[1];
    slot->overflow_logged = false;
}
static void record_transition(relay_device_slot_t *slot, int64_t now_us) {
    while (slot->pending_count && now_us - slot->pending[0].observed_us > SWITCHBOT_PENDING_TTL_US) {
        ESP_LOGW(TAG, "Expired pending BLE state observation");
        pop_pending(slot);
    }
    const relay_sample_t *sample = &slot->latest;
    if (!sample->state_kind) return;
    if (slot->pending_count) {
        relay_sample_t *tail = &slot->pending[slot->pending_count - 1];
        if (tail->state_kind == sample->state_kind && tail->state == sample->state) {
            /* Complete ADV/SCAN_RSP without extending the pending event TTL. */
            if (now_us - tail->observed_us <= SWITCHBOT_FRAGMENT_JOIN_WINDOW_US) {
                int64_t original = tail->observed_us;
                *tail = *sample;
                tail->observed_us = original;
            }
            return;
        }
    } else if (slot->has_published && slot->published_state_kind == sample->state_kind &&
               slot->published_state == sample->state) return;
    if (slot->pending_count == SWITCHBOT_PENDING_STATES) {
        if (!slot->overflow_logged) ESP_LOGW(TAG, "BLE state queue full; newest transition deferred to next advertisement");
        slot->overflow_logged = true;
        return;
    }
    slot->pending[slot->pending_count++] = *sample;
}
static bool periodic_due(const relay_device_slot_t *slot, int64_t now_us) {
    int64_t interval = slot->latest.state_kind ? SWITCHBOT_STATE_INTERVAL_US : SWITCHBOT_RELAY_INTERVAL_US;
    return !slot->has_published || now_us - slot->last_publish_us >= interval ||
        (!slot->published_has_manufacturer && slot->latest.manufacturer_length) ||
        (!slot->published_has_service && slot->latest.service_length);
}
static esp_err_t publish_sample(relay_device_slot_t *slot, const relay_sample_t *sample, int64_t now_us) {
    char relay_node_id[OMK_NODE_ID_HEX_LENGTH + 1], ble_address[13];
    esp_err_t err = node_identity_get_id_hex(relay_node_id, sizeof(relay_node_id));
    if (err == ESP_OK) {
        (void)snprintf(ble_address, sizeof(ble_address), "%02x%02x%02x%02x%02x%02x",
                       slot->address[0], slot->address[1], slot->address[2],
                       slot->address[3], slot->address[4], slot->address[5]);
        err = node_messages_publish_ble_relay(relay_node_id, ble_address, sample->rssi,
                    sample->manufacturer_data, sample->manufacturer_length,
                    sample->service_data, sample->service_length);
    }
    if (err != ESP_OK) {
        slot->retry_after_us = now_us + SWITCHBOT_RELAY_RETRY_US;
        return err;
    }
    slot->retry_after_us = 0;
    slot->has_published = true;
    slot->published_has_manufacturer = sample->manufacturer_length != 0;
    slot->published_has_service = sample->service_length != 0;
    slot->published_state_kind = sample->state_kind;
    slot->published_state = sample->state;
    slot->last_publish_us = now_us;
    return ESP_OK;
}
void switchbot_relay_handle_observation(const uint8_t address[6], int rssi,
        const uint8_t *manufacturer_data, size_t manufacturer_length,
        const uint8_t *service_data, size_t service_length) {
    if (address == NULL || manufacturer_length > 31 || service_length > 31 ||
        (manufacturer_length && manufacturer_data == NULL) ||
        (service_length && service_data == NULL) || (!manufacturer_length && !service_length)) return;
    int64_t now_us = esp_timer_get_time();
    relay_device_slot_t *slot = find_slot(address, now_us);
    if (slot == NULL) { ESP_LOGW(TAG, "Relay device slots exhausted; ignoring BLE observation"); return; }
    if (now_us - slot->manufacturer_us > SWITCHBOT_FRAGMENT_JOIN_WINDOW_US)
        slot->latest.manufacturer_length = 0;
    if (now_us - slot->service_us > SWITCHBOT_FRAGMENT_JOIN_WINDOW_US)
        slot->latest.service_length = 0;
    if (manufacturer_length) {
        memcpy(slot->latest.manufacturer_data, manufacturer_data, manufacturer_length);
        slot->latest.manufacturer_length = (uint8_t)manufacturer_length;
        slot->manufacturer_us = now_us;
    }
    if (service_length) {
        memcpy(slot->latest.service_data, service_data, service_length);
        slot->latest.service_length = (uint8_t)service_length;
        slot->service_us = now_us;
    }
    slot->last_fragment_us = now_us;
    slot->latest.observed_us = now_us;
    slot->latest.rssi = rssi;
    state_hint(&slot->latest);
    record_transition(slot, now_us);
    if (now_us < slot->retry_after_us) return;
    bool sent_pending = slot->pending_count != 0;
    while (slot->pending_count) {
        if (publish_sample(slot, &slot->pending[0], now_us) != ESP_OK) return;
        pop_pending(slot);
    }
    if (!sent_pending && periodic_due(slot, now_us)) (void)publish_sample(slot, &slot->latest, now_us);
}
