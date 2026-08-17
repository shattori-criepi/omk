#include "sensor_manager.h"

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "mqtt_registration.h"
#include "node_registration.h"
#include "sen66_sensor.h"

static const char *TAG = "sensor_manager";

#ifndef OMK_SENSOR_I2C_SDA_GPIO
#define OMK_SENSOR_I2C_SDA_GPIO 2
#endif

#ifndef OMK_SENSOR_I2C_SCL_GPIO
#define OMK_SENSOR_I2C_SCL_GPIO 1
#endif

#define SENSOR_I2C_FREQUENCY_HZ 100000
#define SEN66_RETRY_INTERVAL_MS 60000
#define SEN66_MEASUREMENT_INTERVAL_MS 10000

static i2c_master_bus_handle_t sensor_i2c_bus;

static void sensor_manager_task(void *arg) {
    (void)arg;
    sen66_sensor_t sen66 = {0};
    unsigned int consecutive_read_failures = 0;
    bool logical_id_warning_logged = false;
    bool mqtt_warning_logged = false;

    for (;;) {
        if (!sen66_sensor_is_measuring(&sen66)) {
            esp_err_t err = sen66_sensor_probe(sensor_i2c_bus, &sen66);
            if (err != ESP_OK) {
                ESP_LOGI(TAG, "SEN66 not detected at 0x6B; retrying in 60 seconds");
            } else {
                ESP_LOGI(TAG, "SEN66 detected at 0x6B");
                err = sen66_sensor_start(sensor_i2c_bus, &sen66);
                if (err == ESP_OK) {
                    ESP_LOGI(TAG, "SEN66 continuous measurement started");
                    consecutive_read_failures = 0;
                } else {
                    ESP_LOGW(TAG, "SEN66 startup failed: %s; retrying in 60 seconds",
                             esp_err_to_name(err));
                }
            }
            vTaskDelay(pdMS_TO_TICKS(sen66_sensor_is_measuring(&sen66)
                                     ? SEN66_MEASUREMENT_INTERVAL_MS
                                     : SEN66_RETRY_INTERVAL_MS));
            continue;
        }

        sen66_measurement_t measurement;
        bool data_ready = false;
        esp_err_t err = sen66_sensor_read(&sen66, &measurement, &data_ready);
        if (err != ESP_OK) {
            ++consecutive_read_failures;
            ESP_LOGW(TAG, "SEN66 read failed: %s (consecutive_failures=%u)",
                     esp_err_to_name(err), consecutive_read_failures);
            if (consecutive_read_failures >= 3) {
                ESP_LOGW(TAG, "Recovering SEN66 after consecutive read failures");
                esp_err_t stop_err = sen66_sensor_stop(&sen66);
                if (stop_err != ESP_OK) {
                    ESP_LOGW(TAG, "SEN66 stop failed during recovery: %s",
                             esp_err_to_name(stop_err));
                }
                consecutive_read_failures = 0;
                vTaskDelay(pdMS_TO_TICKS(SEN66_RETRY_INTERVAL_MS));
                continue;
            }
        } else if (data_ready) {
            consecutive_read_failures = 0;
            char logical_id[OMK_NODE_LOGICAL_ID_MAX_LENGTH + 1];
            err = node_registration_get_logical_id(logical_id, sizeof(logical_id));
            if (err != ESP_OK) {
                if (!logical_id_warning_logged) {
                    ESP_LOGI(TAG, "SEN66 publish waiting for logical ID: %s",
                             esp_err_to_name(err));
                    logical_id_warning_logged = true;
                }
            } else {
                logical_id_warning_logged = false;
                err = mqtt_registration_publish_sen66(logical_id, &measurement);
                if (err != ESP_OK && !mqtt_warning_logged) {
                    ESP_LOGW(TAG, "SEN66 MQTT publish unavailable: %s", esp_err_to_name(err));
                    mqtt_warning_logged = true;
                } else if (err == ESP_OK) {
                    mqtt_warning_logged = false;
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(SEN66_MEASUREMENT_INTERVAL_MS));
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
