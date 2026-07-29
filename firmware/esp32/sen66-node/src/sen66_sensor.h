#pragma once

#include <Arduino.h>
#include <SensirionI2cSen66.h>
#include <Wire.h>

struct Sen66Measurement {
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
};

class Sen66Sensor {
  public:
    explicit Sen66Sensor(TwoWire& wire);

    bool begin();
    bool startMeasurement();
    bool stopMeasurement();
    bool readMeasurement(Sen66Measurement& measurement);
    bool isReady() const;
    const char* lastError() const;
    const char* productName() const;
    const char* serialNumber() const;
    uint8_t firmwareMajor() const;
    uint8_t firmwareMinor() const;

  private:
    bool isConnected();
    bool execute(int16_t error, const char* operation);
    void setError(const char* message);

    TwoWire& wire_;
    SensirionI2cSen66 sensor_;
    bool initialized_ = false;
    bool measuring_ = false;
    char last_error_[80] = "not initialized";
    int8_t product_name_[32] = {};
    int8_t serial_number_[32] = {};
    uint8_t firmware_major_ = 0;
    uint8_t firmware_minor_ = 0;
};
