#include "mqtt_publisher.h"

#include <math.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include "app_config.h"
#include "network_config.h"

namespace {
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
constexpr uint32_t MQTT_RETRY_INTERVAL_MS = 5000;
constexpr size_t MQTT_BUFFER_SIZE = 768;

bool appendText(char* buffer, size_t buffer_size, size_t& offset,
                const char* format, ...) {
    va_list arguments;
    va_start(arguments, format);
    const int written = vsnprintf(buffer + offset, buffer_size - offset, format,
                                  arguments);
    va_end(arguments);
    if (written < 0 || static_cast<size_t>(written) >= buffer_size - offset) {
        return false;
    }
    offset += static_cast<size_t>(written);
    return true;
}

bool appendJsonFloat(char* buffer, size_t buffer_size, size_t& offset,
                     const char* name, float value, bool trailing_comma = true) {
    if (!appendText(buffer, buffer_size, offset, "\"%s\":", name)) {
        return false;
    }
    if (!isfinite(value)) {
        return appendText(buffer, buffer_size, offset, "%s", trailing_comma ? "null," : "null");
    }
    return appendText(buffer, buffer_size, offset, trailing_comma ? "%.2f," : "%.2f", value);
}
}  // namespace

MqttPublisher::MqttPublisher() : mqtt_client_(wifi_client_) {
}

void MqttPublisher::begin() {
    buildTopics();
    mqtt_client_.setServer(NetworkConfig::MQTT_HOST, NetworkConfig::MQTT_PORT);
    mqtt_client_.setBufferSize(MQTT_BUFFER_SIZE);
    mqtt_client_.setSocketTimeout(1);
    WiFi.mode(WIFI_STA);
    last_wifi_attempt_ms_ = millis() - WIFI_RETRY_INTERVAL_MS;
    Serial.println("[INFO] Wi-Fi/MQTT publisher initialized");
}

void MqttPublisher::update() {
    const uint32_t now = millis();
    const bool wifi_connected = WiFi.status() == WL_CONNECTED;
    if (!wifi_connected) {
        if (was_wifi_connected_) {
            Serial.println("[WARN] Wi-Fi disconnected");
        }
        was_wifi_connected_ = false;
        if (was_mqtt_connected_) {
            Serial.printf("[WARN] MQTT disconnected: state=%d\n", mqtt_client_.state());
            was_mqtt_connected_ = false;
        }
        if (now - last_wifi_attempt_ms_ >= WIFI_RETRY_INTERVAL_MS) {
            last_wifi_attempt_ms_ = now;
            Serial.printf("[INFO] Wi-Fi connecting: SSID=%s\n", NetworkConfig::WIFI_SSID);
            WiFi.begin(NetworkConfig::WIFI_SSID, NetworkConfig::WIFI_PASSWORD);
        }
        return;
    }

    if (!was_wifi_connected_) {
        Serial.printf("[INFO] Wi-Fi connected: IP=%s RSSI=%d\n",
                      WiFi.localIP().toString().c_str(), WiFi.RSSI());
        was_wifi_connected_ = true;
        last_mqtt_attempt_ms_ = now - MQTT_RETRY_INTERVAL_MS;
    }

    if (!mqtt_client_.connected()) {
        if (was_mqtt_connected_) {
            Serial.printf("[WARN] MQTT disconnected: state=%d\n", mqtt_client_.state());
            was_mqtt_connected_ = false;
        }
        if (now - last_mqtt_attempt_ms_ >= MQTT_RETRY_INTERVAL_MS) {
            last_mqtt_attempt_ms_ = now;
            connectMqtt();
        }
        return;
    }

    was_mqtt_connected_ = true;
    mqtt_client_.loop();
}

bool MqttPublisher::publishMeasurement(const Sen66Measurement& measurement) {
    if (!mqtt_client_.connected()) {
        return false;
    }

    char payload[MQTT_BUFFER_SIZE] = {};
    size_t offset = 0;
    const bool complete =
        appendText(payload, sizeof(payload), offset,
                   "{\"device_id\":\"%s\",\"uptime_ms\":%lu,",
                   NetworkConfig::DEVICE_ID,
                   static_cast<unsigned long>(measurement.uptime_ms)) &&
        appendJsonFloat(payload, sizeof(payload), offset, "pm1_0_ug_m3", measurement.pm1_0_ug_m3) &&
        appendJsonFloat(payload, sizeof(payload), offset, "pm2_5_ug_m3", measurement.pm2_5_ug_m3) &&
        appendJsonFloat(payload, sizeof(payload), offset, "pm4_0_ug_m3", measurement.pm4_0_ug_m3) &&
        appendJsonFloat(payload, sizeof(payload), offset, "pm10_0_ug_m3", measurement.pm10_0_ug_m3) &&
        appendJsonFloat(payload, sizeof(payload), offset, "relative_humidity_percent", measurement.relative_humidity_percent) &&
        appendJsonFloat(payload, sizeof(payload), offset, "temperature_celsius", measurement.temperature_celsius) &&
        appendJsonFloat(payload, sizeof(payload), offset, "voc_index", measurement.voc_index) &&
        appendJsonFloat(payload, sizeof(payload), offset, "nox_index", measurement.nox_index) &&
        appendJsonFloat(payload, sizeof(payload), offset, "co2_ppm", measurement.co2_ppm, false) &&
        appendText(payload, sizeof(payload), offset, "}");
    if (!complete) {
        Serial.println("[WARN] MQTT publish skipped: payload exceeds buffer");
        return false;
    }

    const bool published = mqtt_client_.publish(measurement_topic_, payload, false);
    if (!published) {
        Serial.printf("[WARN] MQTT publish failed: topic=%s\n", measurement_topic_);
    }
    return published;
}

bool MqttPublisher::connectMqtt() {
    Serial.printf("[INFO] MQTT connecting: %s:%u\n", NetworkConfig::MQTT_HOST,
                  NetworkConfig::MQTT_PORT);
    char will_payload[96] = {};
    snprintf(will_payload, sizeof(will_payload),
             "{\"device_id\":\"%s\",\"status\":\"offline\"}",
             NetworkConfig::DEVICE_ID);
    if (!mqtt_client_.connect(mqtt_client_id_, status_topic_, 0, true, will_payload)) {
        Serial.printf("[WARN] MQTT disconnected: state=%d\n", mqtt_client_.state());
        return false;
    }

    was_mqtt_connected_ = true;
    Serial.println("[INFO] MQTT connected");
    return publishOnlineStatus();
}

bool MqttPublisher::publishOnlineStatus() {
    char payload[192] = {};
    snprintf(payload, sizeof(payload),
             "{\"device_id\":\"%s\",\"status\":\"online\","
             "\"firmware_name\":\"%s\",\"firmware_version\":\"%s\"}",
             NetworkConfig::DEVICE_ID, AppConfig::FIRMWARE_NAME,
             AppConfig::FIRMWARE_VERSION);
    if (!mqtt_client_.publish(status_topic_, payload, true)) {
        Serial.printf("[WARN] MQTT publish failed: topic=%s\n", status_topic_);
        return false;
    }
    return true;
}

void MqttPublisher::buildTopics() {
    snprintf(measurement_topic_, sizeof(measurement_topic_), "omk/%s/sen66",
             NetworkConfig::DEVICE_ID);
    snprintf(status_topic_, sizeof(status_topic_), "omk/%s/status",
             NetworkConfig::DEVICE_ID);
    snprintf(mqtt_client_id_, sizeof(mqtt_client_id_), "omk-sen66-%s",
             NetworkConfig::DEVICE_ID);
}
