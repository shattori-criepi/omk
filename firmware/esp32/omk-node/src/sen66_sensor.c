#include "sen66_sensor.h"

#include <math.h>
#include <string.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define SEN66_I2C_ADDRESS 0x6B
#define SEN66_I2C_FREQUENCY_HZ 100000
#define SEN66_I2C_TIMEOUT_MS 100
#define SEN66_DEVICE_RESET_COMMAND 0xD304
#define SEN66_START_CONTINUOUS_MEASUREMENT_COMMAND 0x0021
#define SEN66_STOP_MEASUREMENT_COMMAND 0x0104
#define SEN66_GET_DATA_READY_COMMAND 0x0202
#define SEN66_READ_MEASURED_VALUES_COMMAND 0x0300
#define SEN66_GET_PRODUCT_NAME_COMMAND 0xD014

static esp_err_t sen66_send_command(i2c_master_dev_handle_t device, uint16_t command) {
    uint8_t buffer[] = {(uint8_t)(command >> 8), (uint8_t)command};
    return i2c_master_transmit(device, buffer, sizeof(buffer), SEN66_I2C_TIMEOUT_MS);
}

static uint8_t sen66_crc8(const uint8_t *data) {
    uint8_t crc = 0xFF;
    for (size_t byte = 0; byte < 2; ++byte) {
        crc ^= data[byte];
        for (uint8_t bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

static esp_err_t sen66_read_response(i2c_master_dev_handle_t device, uint16_t command,
                                     uint8_t *values, size_t value_size) {
    if (value_size == 0 || value_size > 32 || value_size % 2 != 0) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = sen66_send_command(device, command);
    if (err != ESP_OK) {
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(20));

    uint8_t response[48];
    memset(response, 0xFF, sizeof(response));
    size_t word_count = value_size / 2;
    err = i2c_master_receive(device, response, word_count * 3, SEN66_I2C_TIMEOUT_MS);
    if (err != ESP_OK) {
        return err;
    }
    for (size_t word = 0; word < word_count; ++word) {
        const uint8_t *response_word = &response[word * 3];
        if (response_word[2] != sen66_crc8(response_word)) {
            return ESP_ERR_INVALID_CRC;
        }
        values[word * 2] = response_word[0];
        values[word * 2 + 1] = response_word[1];
    }
    return ESP_OK;
}

static uint16_t sen66_u16(const uint8_t *value) {
    return ((uint16_t)value[0] << 8) | value[1];
}

static sensor_identity_t identify_device(i2c_master_dev_handle_t device) {
    uint8_t name[32];
    if (sen66_read_response(device, SEN66_GET_PRODUCT_NAME_COMMAND, name, sizeof(name)) != ESP_OK)
        return SENSOR_ID_ERROR;
    /* SEN6x datasheet 4.8.18: string<32>, ASCII, null terminated, CRC/word.
     * Padding after NUL is unspecified. Never accept prefixes as exact models. */
    size_t length = 0;
    while (length < sizeof(name) && name[length] != 0) {
        if (name[length] < 0x20 || name[length] > 0x7E) return SENSOR_ID_ERROR;
        ++length;
    }
    if (length == 0 || length == sizeof(name)) return SENSOR_ID_ERROR;
    if (strcmp((char *)name, "SEN66") == 0) return SENSOR_ID_EXACT;
    if (strcmp((char *)name, "SEN63C") == 0 || strcmp((char *)name, "SEN65") == 0 ||
        strcmp((char *)name, "SEN68") == 0) return SENSOR_ID_AMBIGUOUS;
    return SENSOR_ID_NO_MATCH;
}

static esp_err_t add_device(i2c_master_bus_handle_t bus, i2c_master_dev_handle_t *device) {
    i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = SEN66_I2C_ADDRESS,
        .scl_speed_hz = SEN66_I2C_FREQUENCY_HZ,
    };
    return i2c_master_bus_add_device(bus, &config, device);
}

sensor_identity_t sen66_sensor_identify(i2c_master_bus_handle_t bus) {
    if (!bus) return SENSOR_ID_ERROR;
    esp_err_t err = i2c_master_probe(bus, SEN66_I2C_ADDRESS, SEN66_I2C_TIMEOUT_MS);
    if (err == ESP_ERR_NOT_FOUND) return SENSOR_ID_NO_MATCH;
    if (err != ESP_OK) return SENSOR_ID_ERROR;
    i2c_master_dev_handle_t device = NULL;
    if (add_device(bus, &device) != ESP_OK) return SENSOR_ID_ERROR;
    sensor_identity_t result = identify_device(device);
    if (i2c_master_bus_rm_device(device) != ESP_OK) return SENSOR_ID_ERROR;
    return result;
}

esp_err_t sen66_sensor_probe(i2c_master_bus_handle_t bus, sen66_sensor_t *sensor) {
    if (bus == NULL || sensor == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(sensor, 0, sizeof(*sensor));
    sensor->state = SEN66_SENSOR_STATE_ABSENT;
    if (sen66_sensor_identify(bus) == SENSOR_ID_EXACT) {
        sensor->state = SEN66_SENSOR_STATE_PROBED;
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t sen66_sensor_start(i2c_master_bus_handle_t bus, sen66_sensor_t *sensor) {
    if (bus == NULL || sensor == NULL || sensor->state != SEN66_SENSOR_STATE_PROBED) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t err = add_device(bus, &sensor->device);
    if (err != ESP_OK) {
        return err;
    }

    /* Revalidate after selection, immediately before the first mutation. */
    if (identify_device(sensor->device) != SENSOR_ID_EXACT) {
        sen66_sensor_close(sensor);
        return ESP_ERR_INVALID_STATE;
    }
    /* Recovery (or an MCU reboot) may leave a confirmed SEN66 measuring.
     * Reset is idle-only. Stop can NACK if already idle; the following identity
     * read and reset must still succeed. Never do this during discovery. */
    (void)sen66_send_command(sensor->device, SEN66_STOP_MEASUREMENT_COMMAND);
    vTaskDelay(pdMS_TO_TICKS(1400));
    if (identify_device(sensor->device) != SENSOR_ID_EXACT) {
        sen66_sensor_close(sensor);
        return ESP_ERR_INVALID_STATE;
    }
    err = sen66_send_command(sensor->device, SEN66_DEVICE_RESET_COMMAND);
    if (err == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(1200));
        err = sen66_send_command(sensor->device, SEN66_START_CONTINUOUS_MEASUREMENT_COMMAND);
    }
    if (err == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(50));
        sensor->state = SEN66_SENSOR_STATE_MEASURING;
        return ESP_OK;
    }

    i2c_master_bus_rm_device(sensor->device);
    sensor->device = NULL;
    sensor->state = SEN66_SENSOR_STATE_ABSENT;
    return err;
}

esp_err_t sen66_sensor_stop(sen66_sensor_t *sensor) {
    if (sensor == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = ESP_OK;
    if (sensor->device != NULL && sensor->state == SEN66_SENSOR_STATE_MEASURING &&
        identify_device(sensor->device) == SENSOR_ID_EXACT) {
        err = sen66_send_command(sensor->device, SEN66_STOP_MEASUREMENT_COMMAND);
        if (err == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(1400));
        }
    }
    if (sensor->device != NULL) {
        i2c_master_bus_rm_device(sensor->device);
    }
    sensor->device = NULL;
    sensor->state = SEN66_SENSOR_STATE_ABSENT;
    return err;
}

esp_err_t sen66_sensor_read(sen66_sensor_t *sensor, sen66_measurement_t *measurement,
                            bool *data_ready) {
    if (sensor == NULL || measurement == NULL || data_ready == NULL ||
        sensor->state != SEN66_SENSOR_STATE_MEASURING) {
        return ESP_ERR_INVALID_STATE;
    }

    *data_ready = false;
    /* Product Name is available during measurement too. A replaced endpoint
     * must not publish under the old SEN66 Logical ID even if values fit. */
    if (identify_device(sensor->device) != SENSOR_ID_EXACT) return ESP_ERR_INVALID_STATE;

    uint8_t ready_response[2];
    esp_err_t err = sen66_read_response(sensor->device, SEN66_GET_DATA_READY_COMMAND,
                                        ready_response, sizeof(ready_response));
    if (err != ESP_OK) {
        return err;
    }
    *data_ready = ready_response[1] != 0;
    if (!*data_ready) {
        return ESP_OK;
    }

    uint8_t values[18];
    err = sen66_read_response(sensor->device, SEN66_READ_MEASURED_VALUES_COMMAND,
                              values, sizeof(values));
    if (err != ESP_OK) {
        return err;
    }

    const uint16_t pm1_0 = sen66_u16(&values[0]);
    const uint16_t pm2_5 = sen66_u16(&values[2]);
    const uint16_t pm4_0 = sen66_u16(&values[4]);
    const uint16_t pm10_0 = sen66_u16(&values[6]);
    const int16_t humidity = (int16_t)sen66_u16(&values[8]);
    const int16_t temperature = (int16_t)sen66_u16(&values[10]);
    const int16_t voc_index = (int16_t)sen66_u16(&values[12]);
    const int16_t nox_index = (int16_t)sen66_u16(&values[14]);
    const uint16_t co2 = sen66_u16(&values[16]);
    *measurement = (sen66_measurement_t){
        .pm1_0_ug_m3 = pm1_0 == UINT16_MAX ? NAN : pm1_0 / 10.0f,
        .pm2_5_ug_m3 = pm2_5 == UINT16_MAX ? NAN : pm2_5 / 10.0f,
        .pm4_0_ug_m3 = pm4_0 == UINT16_MAX ? NAN : pm4_0 / 10.0f,
        .pm10_0_ug_m3 = pm10_0 == UINT16_MAX ? NAN : pm10_0 / 10.0f,
        .relative_humidity_percent = humidity == INT16_MAX ? NAN : humidity / 100.0f,
        .temperature_celsius = temperature == INT16_MAX ? NAN : temperature / 200.0f,
        .voc_index = voc_index == INT16_MAX ? NAN : voc_index / 10.0f,
        .nox_index = nox_index == INT16_MAX ? NAN : nox_index / 10.0f,
        .co2_ppm = co2 == UINT16_MAX ? NAN : (float)co2,
        .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
    };
    return ESP_OK;
}

bool sen66_sensor_is_measuring(const sen66_sensor_t *sensor) {
    return sensor != NULL && sensor->state == SEN66_SENSOR_STATE_MEASURING;
}

void sen66_sensor_close(sen66_sensor_t *sensor) {
    if (!sensor) return;
    if (sensor->device) i2c_master_bus_rm_device(sensor->device);
    sensor->device = NULL;
    sensor->state = SEN66_SENSOR_STATE_ABSENT;
}
