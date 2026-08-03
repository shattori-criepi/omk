#include <Arduino.h>

#include "sen66_sensor.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "app_config.h"

namespace {
constexpr int16_t NO_ERROR = 0;
constexpr uint16_t INVALID_UINT16 = 0xFFFF;
constexpr int16_t INVALID_INT16 = 0x7FFF;
}  // namespace

Sen66Sensor::Sen66Sensor(TwoWire& wire) : wire_(wire) {
}

bool Sen66Sensor::begin() {
    initialized_ = false;
    measuring_ = false;

    if (!isConnected()) {
        setError("SEN66 not found at I2C address 0x6B");
        return false;
    }

    sensor_.begin(wire_, AppConfig::SEN66_I2C_ADDRESS);
    if (!execute(sensor_.deviceReset(), "device reset")) {
        return false;
    }
    delay(AppConfig::SENSOR_RESET_WAIT_MS);

    if (!execute(sensor_.getProductName(product_name_, sizeof(product_name_)),
                 "get product name") ||
        !execute(sensor_.getSerialNumber(serial_number_, sizeof(serial_number_)),
                 "get serial number") ||
        !execute(sensor_.getVersion(firmware_major_, firmware_minor_),
                 "get firmware version")) {
        return false;
    }

    initialized_ = true;
    setError("");
    return true;
}

bool Sen66Sensor::startMeasurement() {
    if (!initialized_) {
        setError("SEN66 has not been initialized");
        return false;
    }
    if (!execute(sensor_.startContinuousMeasurement(),
                 "start continuous measurement")) {
        return false;
    }
    measuring_ = true;
    return true;
}

bool Sen66Sensor::stopMeasurement() {
    if (!measuring_) {
        return true;
    }
    if (!execute(sensor_.stopMeasurement(), "stop measurement")) {
        return false;
    }
    measuring_ = false;
    return true;
}

bool Sen66Sensor::readMeasurement(Sen66Measurement& measurement) {
    if (!measuring_) {
        setError("continuous measurement is not running");
        return false;
    }

    uint8_t padding = 0;
    bool data_ready = false;
    if (!execute(sensor_.getDataReady(padding, data_ready), "get data ready")) {
        return false;
    }
    if (!data_ready) {
        setError("measurement data not ready");
        return false;
    }

    uint16_t pm1_0 = 0;
    uint16_t pm2_5 = 0;
    uint16_t pm4_0 = 0;
    uint16_t pm10_0 = 0;
    int16_t humidity = 0;
    int16_t temperature = 0;
    int16_t voc_index = 0;
    int16_t nox_index = 0;
    uint16_t co2 = 0;
    if (!execute(sensor_.readMeasuredValuesAsIntegers(
                     pm1_0, pm2_5, pm4_0, pm10_0, humidity, temperature,
                     voc_index, nox_index, co2),
                 "read measured values")) {
        return false;
    }

    measurement = {
        pm1_0 == INVALID_UINT16 ? NAN : pm1_0 / 10.0F,
        pm2_5 == INVALID_UINT16 ? NAN : pm2_5 / 10.0F,
        pm4_0 == INVALID_UINT16 ? NAN : pm4_0 / 10.0F,
        pm10_0 == INVALID_UINT16 ? NAN : pm10_0 / 10.0F,
        humidity == INVALID_INT16 ? NAN : humidity / 100.0F,
        temperature == INVALID_INT16 ? NAN : temperature / 200.0F,
        voc_index == INVALID_INT16 ? NAN : voc_index / 10.0F,
        nox_index == INVALID_INT16 ? NAN : nox_index / 10.0F,
        co2 == INVALID_UINT16 ? NAN : static_cast<float>(co2),
        millis(),
    };
    setError("");
    return true;
}

bool Sen66Sensor::isReady() const {
    return initialized_ && measuring_;
}

const char* Sen66Sensor::lastError() const {
    return last_error_;
}

const char* Sen66Sensor::productName() const {
    return reinterpret_cast<const char*>(product_name_);
}

const char* Sen66Sensor::serialNumber() const {
    return reinterpret_cast<const char*>(serial_number_);
}

uint8_t Sen66Sensor::firmwareMajor() const {
    return firmware_major_;
}

uint8_t Sen66Sensor::firmwareMinor() const {
    return firmware_minor_;
}

bool Sen66Sensor::isConnected() {
    wire_.beginTransmission(AppConfig::SEN66_I2C_ADDRESS);
    return wire_.endTransmission() == 0;
}

bool Sen66Sensor::execute(int16_t error, const char* operation) {
    if (error == NO_ERROR) {
        return true;
    }
    snprintf(last_error_, sizeof(last_error_), "%s failed: error_code=%d",
             operation, static_cast<int>(error));
    return false;
}

void Sen66Sensor::setError(const char* message) {
    snprintf(last_error_, sizeof(last_error_), "%s", message);
}
