#include "sensor_manager.h"

#include <inttypes.h>
#include <stdlib.h>
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "node_registration.h"
#include "sensor_driver.h"

static const char *TAG = "sensor_manager";
#ifndef OMK_SENSOR_I2C_SDA_GPIO
#define OMK_SENSOR_I2C_SDA_GPIO 2
#endif
#ifndef OMK_SENSOR_I2C_SCL_GPIO
#define OMK_SENSOR_I2C_SCL_GPIO 1
#endif
#define SENSOR_I2C_FREQUENCY_HZ 100000
#define SENSOR_RETRY_INTERVAL_MS 60000
#define SENSOR_MEASUREMENT_INTERVAL_MS 10000
#define SENSOR_MEASUREMENT_LIVENESS_TIMEOUT_MS 60000

static i2c_master_bus_handle_t sensor_i2c_bus;
typedef struct {
    sensor_endpoint_t endpoint;
    const sensor_driver_t *driver;
    void *context;
    unsigned consecutive_read_failures;
    bool logical_id_warning_logged;
    bool mqtt_warning_logged;
    bool recovery_pending;
    uint32_t recovery_count;
    uint32_t measurement_timeout_count;
    uint64_t last_successful_measurement_ms;
    uint64_t next_attempt_ms;
} sensor_slot_t;

static uint64_t monotonic_milliseconds(void) {
    return (uint64_t)(esp_timer_get_time() / 1000);
}

static void recover_sensor(sensor_slot_t *slot, const char *reason) {
    ESP_LOGW(TAG, "Recovering %s after %s", slot->driver->driver_id, reason);
    ++slot->recovery_count;
    slot->driver->diagnostics(slot->recovery_count, slot->measurement_timeout_count);
    slot->driver->connected(false);
    // The endpoint may now contain a different sensor. Release locally only.
    slot->driver->close(slot->context);
    free(slot->context);
    slot->context = NULL;
    slot->driver = NULL;
    slot->consecutive_read_failures = 0;
    slot->recovery_pending = true;
    slot->next_attempt_ms = monotonic_milliseconds() + SENSOR_RETRY_INTERVAL_MS;
}

static void sensor_slot_tick(sensor_slot_t *slot) {
    if (monotonic_milliseconds() < slot->next_attempt_ms) return;
    if (!slot->driver) {
        const sensor_driver_t *selected = NULL;
        sensor_identity_t identity = sensor_discover(&slot->endpoint, sensor_drivers,
                                                     sensor_driver_count, &selected);
        slot->next_attempt_ms = monotonic_milliseconds() + SENSOR_RETRY_INTERVAL_MS;
        if (identity != SENSOR_ID_EXACT) {
            ESP_LOGI(TAG, "Sensor identity unresolved (%d); retrying in 60 seconds", identity);
            return;
        }
        void *context = calloc(1, selected->context_size);
        if (!context) return;
        esp_err_t err = selected->start(&slot->endpoint, context);
        if (err != ESP_OK) {
            selected->connected(false);
            selected->close(context);
            free(context);
            slot->next_attempt_ms = monotonic_milliseconds() + SENSOR_RETRY_INTERVAL_MS;
            ESP_LOGW(TAG, "%s startup failed: %s", selected->driver_id, esp_err_to_name(err));
            return;
        }
        slot->driver = selected;
        slot->context = context;
        selected->connected(true);
        slot->consecutive_read_failures = 0;
        slot->last_successful_measurement_ms = monotonic_milliseconds();
        slot->next_attempt_ms = slot->last_successful_measurement_ms + SENSOR_MEASUREMENT_INTERVAL_MS;
        ESP_LOGI(TAG, "%s continuous measurement started", selected->driver_id);
        return;
    }

    bool data_ready = false;
    esp_err_t err = slot->driver->read(slot->context, &data_ready);
    if (err != ESP_OK) {
        ++slot->consecutive_read_failures;
        ESP_LOGW(TAG, "%s read failed: %s (consecutive_failures=%u)",
                 slot->driver->driver_id, esp_err_to_name(err), slot->consecutive_read_failures);
        // Loss of identity is immediate; ordinary transient I/O retains the
        // existing three-failure recovery policy.
        if (err == ESP_ERR_INVALID_STATE || slot->consecutive_read_failures >= 3) {
            recover_sensor(slot, "identity lost or consecutive read failures");
            return;
        }
    } else if (data_ready) {
        slot->consecutive_read_failures = 0;
        slot->last_successful_measurement_ms = monotonic_milliseconds();
        if (slot->recovery_pending) {
            ESP_LOGI(TAG, "%s measurement recovered", slot->driver->driver_id);
            slot->recovery_pending = false;
        }
        char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
        err = node_registration_get_logical_id(logical_id, sizeof(logical_id));
        if (err != ESP_OK) {
            if (!slot->logical_id_warning_logged) {
                ESP_LOGI(TAG, "%s publish waiting for logical ID", slot->driver->driver_id);
                slot->logical_id_warning_logged = true;
            }
        } else {
            slot->logical_id_warning_logged = false;
            err = slot->driver->publish(slot->context, logical_id);
            if (err != ESP_OK && !slot->mqtt_warning_logged) {
                ESP_LOGW(TAG, "%s MQTT publish unavailable: %s", slot->driver->driver_id, esp_err_to_name(err));
                slot->mqtt_warning_logged = true;
            } else if (err == ESP_OK) {
                slot->mqtt_warning_logged = false;
            }
        }
    }
    if (monotonic_milliseconds() - slot->last_successful_measurement_ms >= SENSOR_MEASUREMENT_LIVENESS_TIMEOUT_MS) {
        ++slot->measurement_timeout_count;
        ESP_LOGW(TAG, "%s measurement timeout: no successful measurement for %" PRIu64 " s; recovering",
                 slot->driver->driver_id,
                 (monotonic_milliseconds() - slot->last_successful_measurement_ms) / 1000);
        recover_sensor(slot, "measurement liveness timeout");
        return;
    }
    slot->next_attempt_ms = monotonic_milliseconds() + SENSOR_MEASUREMENT_INTERVAL_MS;
}

static void sensor_manager_task(void *arg) {
    (void)arg;
    // Current board profile has one candidate endpoint. More slots do not
    // require model branches; multi-sensor Logical IDs remain a future change.
    sensor_slot_t slots[] = {{ .endpoint = {
        .transport = SENSOR_TRANSPORT_I2C, .handle = sensor_i2c_bus,
        .location.i2c_address = 0x6B,
    } }};
    for (;;) {
        for (size_t i = 0; i < sizeof(slots) / sizeof(slots[0]); ++i) sensor_slot_tick(&slots[i]);
        uint64_t now = monotonic_milliseconds();
        uint64_t wait_ms = SENSOR_RETRY_INTERVAL_MS;
        for (size_t i = 0; i < sizeof(slots) / sizeof(slots[0]); ++i) {
            uint64_t remaining = slots[i].next_attempt_ms > now ? slots[i].next_attempt_ms - now : 1;
            if (remaining < wait_ms) wait_ms = remaining;
        }
        vTaskDelay(pdMS_TO_TICKS(wait_ms) ? pdMS_TO_TICKS(wait_ms) : 1);
    }
}

esp_err_t sensor_manager_start(void) {
    if (sensor_i2c_bus != NULL) {
        return ESP_OK;
    }

    i2c_master_bus_config_t bus_config = {
        .i2c_port = -1,
        .sda_io_num = OMK_SENSOR_I2C_SDA_GPIO,
        .scl_io_num = OMK_SENSOR_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &sensor_i2c_bus);
    if (err != ESP_OK) {
        return err;
    }

    if (xTaskCreate(sensor_manager_task, "sensor_manager", 3072, NULL, 5, NULL) != pdPASS) {
        i2c_del_master_bus(sensor_i2c_bus);
        sensor_i2c_bus = NULL;
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Sensor manager started (I2C SDA=%d SCL=%d %d Hz)",
             OMK_SENSOR_I2C_SDA_GPIO, OMK_SENSOR_I2C_SCL_GPIO, SENSOR_I2C_FREQUENCY_HZ);
    return ESP_OK;
}
