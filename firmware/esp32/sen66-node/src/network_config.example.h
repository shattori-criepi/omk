#pragma once

#include <Arduino.h>

namespace NetworkConfig {

constexpr char WIFI_SSID[] = "OMK-XXXXXX";
constexpr char WIFI_PASSWORD[] = "replace-me";

constexpr char MQTT_HOST[] = "192.168.50.1";
constexpr uint16_t MQTT_PORT = 1883;

constexpr char DEVICE_ID[] = "sen66-001";

}  // namespace NetworkConfig
