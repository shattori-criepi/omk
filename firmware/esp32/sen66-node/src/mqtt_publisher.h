#pragma once

#include <Arduino.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "sen66_sensor.h"

class MqttPublisher {
  public:
    MqttPublisher();

    void begin();
    void update();
    bool publishMeasurement(const Sen66Measurement& measurement);

  private:
    bool connectMqtt();
    bool publishOnlineStatus();
    void buildTopics();

    WiFiClient wifi_client_;
    PubSubClient mqtt_client_;
    uint32_t last_wifi_attempt_ms_ = 0;
    uint32_t last_mqtt_attempt_ms_ = 0;
    bool was_wifi_connected_ = false;
    bool was_mqtt_connected_ = false;
    char measurement_topic_[96] = {};
    char status_topic_[96] = {};
    char mqtt_client_id_[64] = {};
};
