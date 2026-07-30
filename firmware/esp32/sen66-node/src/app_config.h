#pragma once

#include <Arduino.h>

namespace AppConfig {

constexpr char FIRMWARE_NAME[] = "omk-sen66-node";
constexpr char FIRMWARE_VERSION[] = "0.1.0";

constexpr uint32_t SERIAL_BAUD_RATE = 115200;

constexpr uint8_t I2C_SDA_PIN = 21;
constexpr uint8_t I2C_SCL_PIN = 22;
constexpr uint8_t SEN66_I2C_ADDRESS = 0x6B;
constexpr uint32_t I2C_FREQUENCY_HZ = 100000;

constexpr uint32_t MEASUREMENT_INTERVAL_MS = 10000;
constexpr uint32_t SENSOR_RETRY_INTERVAL_MS = 5000;
constexpr uint8_t MAX_CONSECUTIVE_READ_FAILURES = 3;
constexpr uint32_t SENSOR_RESET_WAIT_MS = 1200;

}  // namespace AppConfig
