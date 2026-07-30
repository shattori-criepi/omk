#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include <string.h>

#include "app_config.h"
#include "mqtt_publisher.h"
#include "sen66_sensor.h"

namespace {
Sen66Sensor sen66(Wire);
MqttPublisher mqtt_publisher;
uint32_t last_measurement_attempt_ms = 0;
uint32_t last_initialization_attempt_ms = 0;
uint8_t consecutive_read_failures = 0;

void printJsonFloat(const char* name, float value, bool trailing_comma = true) {
    Serial.print('\"');
    Serial.print(name);
    Serial.print("\":");
    if (isnan(value)) {
        Serial.print("null");
    } else {
        Serial.print(value, 2);
    }
    if (trailing_comma) {
        Serial.print(',');
    }
}

void printMeasurement(const Sen66Measurement& measurement) {
    Serial.print("{\"type\":\"measurement\",\"uptime_ms\":");
    Serial.print(measurement.uptime_ms);
    Serial.print(',');
    printJsonFloat("pm1_0_ug_m3", measurement.pm1_0_ug_m3);
    printJsonFloat("pm2_5_ug_m3", measurement.pm2_5_ug_m3);
    printJsonFloat("pm4_0_ug_m3", measurement.pm4_0_ug_m3);
    printJsonFloat("pm10_0_ug_m3", measurement.pm10_0_ug_m3);
    printJsonFloat("relative_humidity_percent", measurement.relative_humidity_percent);
    printJsonFloat("temperature_celsius", measurement.temperature_celsius);
    printJsonFloat("voc_index", measurement.voc_index);
    printJsonFloat("nox_index", measurement.nox_index);
    printJsonFloat("co2_ppm", measurement.co2_ppm, false);
    Serial.println('}');
}

bool initializeSensor() {
    last_initialization_attempt_ms = millis();
    if (!sen66.begin()) {
        Serial.print("[ERROR] SEN66 initialization failed: ");
        Serial.println(sen66.lastError());
        return false;
    }

    Serial.println("[INFO] SEN66 detected at 0x6B");
    Serial.print("[INFO] SEN66 product name: ");
    Serial.println(sen66.productName());
    Serial.print("[INFO] SEN66 serial number: ");
    Serial.println(sen66.serialNumber());
    Serial.printf("[INFO] SEN66 firmware version: %u.%u\n", sen66.firmwareMajor(),
                  sen66.firmwareMinor());

    if (!sen66.startMeasurement()) {
        Serial.print("[ERROR] Continuous measurement start failed: ");
        Serial.println(sen66.lastError());
        return false;
    }
    consecutive_read_failures = 0;
    Serial.println("[INFO] Continuous measurement started");
    return true;
}

void handleMeasurement() {
    Sen66Measurement measurement{};
    if (sen66.readMeasurement(measurement)) {
        consecutive_read_failures = 0;
        printMeasurement(measurement);
        mqtt_publisher.publishMeasurement(measurement);
        return;
    }

    if (strcmp(sen66.lastError(), "measurement data not ready") == 0) {
        Serial.println("[WARN] Measurement data not ready");
        return;
    }

    ++consecutive_read_failures;
    Serial.printf("[ERROR] SEN66 read failed: %s (consecutive_failures=%u)\n",
                  sen66.lastError(), consecutive_read_failures);
    if (consecutive_read_failures < AppConfig::MAX_CONSECUTIVE_READ_FAILURES) {
        return;
    }

    Serial.println("[WARN] Attempting SEN66 recovery");
    sen66.stopMeasurement();
    initializeSensor();
}
}  // namespace

void setup() {
    Serial.begin(AppConfig::SERIAL_BAUD_RATE);
    delay(100);
    Serial.println("[INFO] OMK SEN66 node starting");
    Serial.print("[INFO] Firmware version: ");
    Serial.println(AppConfig::FIRMWARE_VERSION);

    Wire.begin(AppConfig::I2C_SDA_PIN, AppConfig::I2C_SCL_PIN,
               AppConfig::I2C_FREQUENCY_HZ);
    Serial.printf("[INFO] I2C initialized: SDA=%u SCL=%u\n",
                  AppConfig::I2C_SDA_PIN, AppConfig::I2C_SCL_PIN);
    initializeSensor();
    mqtt_publisher.begin();
}

void loop() {
    mqtt_publisher.update();
    const uint32_t now = millis();
    if (!sen66.isReady()) {
        if (now - last_initialization_attempt_ms >=
            AppConfig::SENSOR_RETRY_INTERVAL_MS) {
            Serial.println("[INFO] Retrying SEN66 initialization");
            initializeSensor();
        }
    } else if (now - last_measurement_attempt_ms >= AppConfig::MEASUREMENT_INTERVAL_MS) {
        last_measurement_attempt_ms = now;
        handleMeasurement();
    }
}
