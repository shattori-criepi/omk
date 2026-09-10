#pragma once

#include <stdbool.h>

#include "driver/i2c_master.h"
#include "esp_err.h"
#include "sensor_driver.h"

typedef enum {
    SEN66_SENSOR_STATE_ABSENT,
    SEN66_SENSOR_STATE_PROBED,
    SEN66_SENSOR_STATE_MEASURING,
} sen66_sensor_state_t;

typedef struct sen66_measurement {
    float pm1_0_ug_m3;
    float pm2_5_ug_m3;
    float pm4_0_ug_m3;
    float pm10_0_ug_m3;
    float relative_humidity_percent;
    float temperature_celsius;
    float voc_index;
    float nox_index;
    float co2_ppm;
    uint32_t uptime_ms;
} sen66_measurement_t;

typedef struct {
    i2c_master_dev_handle_t device;
    sen66_sensor_state_t state;
} sen66_sensor_t;

esp_err_t sen66_sensor_probe(i2c_master_bus_handle_t bus, sen66_sensor_t *sensor);
sensor_identity_t sen66_sensor_identify(i2c_master_bus_handle_t bus);
void sen66_sensor_close(sen66_sensor_t *sensor);
esp_err_t sen66_sensor_start(i2c_master_bus_handle_t bus, sen66_sensor_t *sensor);
esp_err_t sen66_sensor_stop(sen66_sensor_t *sensor);
esp_err_t sen66_sensor_read(sen66_sensor_t *sensor, sen66_measurement_t *measurement,
                            bool *data_ready);
bool sen66_sensor_is_measuring(const sen66_sensor_t *sensor);
