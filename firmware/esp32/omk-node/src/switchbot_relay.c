#include "switchbot_relay.h"

#include <stdbool.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

#include "mqtt_registration.h"

static const char *TAG = "switchbot_relay";

/* The target is intentionally fixed for this first end-to-end validation.
 * A future registry will replace both this physical identifier check and the
 * temporary logical sensor ID. */
static const uint8_t TARGET_METER_PHYSICAL_ID[6] = {
    0xcf, 0x39, 0x41, 0xc7, 0xed, 0x79,
};
static const char *TARGET_SENSOR_ID = "switchbot-meter-001";

#define SWITCHBOT_METER_MANUFACTURER_LENGTH 11
#define SWITCHBOT_METER_LAYOUT_MARKER_INDEX 7
#define SWITCHBOT_METER_LAYOUT_MARKER 0x03
#define SWITCHBOT_METER_MEASUREMENT_OFFSET 8
#define SWITCHBOT_ENVIRONMENT_MIN_TEMPERATURE_C (-20.0f)
#define SWITCHBOT_ENVIRONMENT_MAX_TEMPERATURE_C 60.0f
#define SWITCHBOT_ENVIRONMENT_PUBLISH_INTERVAL_US (10LL * 1000LL * 1000LL)

static int64_t last_publish_attempt_us;

static bool decode_target_meter(const uint8_t *data, size_t length,
                                float *temperature_c, uint8_t *humidity_percent) {
    if (data == NULL || length != SWITCHBOT_METER_MANUFACTURER_LENGTH ||
        memcmp(data, TARGET_METER_PHYSICAL_ID, sizeof(TARGET_METER_PHYSICAL_ID)) != 0 ||
        data[SWITCHBOT_METER_LAYOUT_MARKER_INDEX] != SWITCHBOT_METER_LAYOUT_MARKER) {
        return false;
    }

    uint8_t fraction = data[SWITCHBOT_METER_MEASUREMENT_OFFSET];
    uint8_t signed_integer = data[SWITCHBOT_METER_MEASUREMENT_OFFSET + 1];
    uint8_t humidity = data[SWITCHBOT_METER_MEASUREMENT_OFFSET + 2];
    if (fraction > 9 || humidity > 100) {
        return false;
    }

    float temperature = (float)(signed_integer & 0x7f) + (float)fraction / 10.0f;
    if ((signed_integer & 0x80) == 0) {
        temperature = -temperature;
    }
    if (temperature < SWITCHBOT_ENVIRONMENT_MIN_TEMPERATURE_C ||
        temperature > SWITCHBOT_ENVIRONMENT_MAX_TEMPERATURE_C) {
        return false;
    }

    *temperature_c = temperature;
    *humidity_percent = humidity;
    return true;
}

void switchbot_relay_handle_manufacturer_data(const uint8_t *data, size_t length) {
    float temperature_c;
    uint8_t humidity_percent;
    if (!decode_target_meter(data, length, &temperature_c, &humidity_percent)) {
        return;
    }

    int64_t now_us = esp_timer_get_time();
    if (last_publish_attempt_us != 0 &&
        now_us - last_publish_attempt_us < SWITCHBOT_ENVIRONMENT_PUBLISH_INTERVAL_US) {
        return;
    }
    last_publish_attempt_us = now_us;

    esp_err_t err = mqtt_registration_publish_environment(
        TARGET_SENSOR_ID, temperature_c, humidity_percent);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Published Meter environment temperature=%.1f humidity=%u",
                 (double)temperature_c, humidity_percent);
    } else if (err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Could not queue Meter environment: %s", esp_err_to_name(err));
    }
}
