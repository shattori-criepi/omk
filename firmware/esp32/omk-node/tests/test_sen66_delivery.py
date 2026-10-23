"""Run the production manager, driver, sender and MQTT registration together.

The MQTT adapter can block a real host thread while a deterministic clock
advances the measurement task for 20 minutes. No radio timing claim is made.
"""
from pathlib import Path
import subprocess

import pytest

PROJECT = Path(__file__).parents[1]
HOST = Path(__file__).parent / "delivery_host"


@pytest.fixture(scope="module", params=[1, 2])
def delivery_binary(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("sen66-delivery")
    # Keep thin IDF header adapters in one place; use the actual public OMK
    # headers and actual cJSON parser, not a second registration implementation.
    for name in ("esp_err.h", "esp_timer.h", "esp_random.h", "esp_log.h",
                 "esp_event.h", "esp_netif.h", "esp_netif_ip_addr.h", "esp_wifi.h", "nvs.h",
                 "mqtt_client.h", "freertos/FreeRTOS.h", "freertos/task.h",
                 "driver/i2c_master.h"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "idf_stubs.h"\n')
    # Run the adopted ESP-MQTT dispatch implementation, not a second copy of
    # its post/run logic. The adapter below models IDF's zero-tick one-event
    # execution and bounded queue; it does not model transport/radio timing.
    vendor = (PROJECT / "managed_components/espressif__mqtt/mqtt_client.c").read_text()
    start = vendor.index("esp_err_t esp_mqtt_dispatch_custom_event(")
    end = vendor.index("static esp_err_t deliver_publish(", start)
    (root / "mqtt_dispatch.inc").write_text(vendor[start:end])
    binary = root / "delivery-test"
    command = ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
               f"-DCONFIG_MQTT_EVENT_QUEUE_SIZE={request.param}",
               "-I", str(root), "-I", str(HOST), "-I", str(PROJECT / "src"),
               "-I", str(PROJECT / "managed_components/espressif__cjson/cJSON"),
               str(HOST / "delivery_test.c"),
               str(PROJECT / "managed_components/espressif__cjson/cJSON/cJSON.c"),
               "-lm", "-o", str(binary)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


@pytest.mark.parametrize("scenario", [
    "broker_outage_recovery", "registration_retry", "subscription_retry", "clear_retry_superseded",
    "enqueue_20min", "outbox_20min", "relay_registration", "startup_failure",
    "replace_expire", "registration_inflight", "sensor_recovery",
    "status_coalesce", "status_failure", "enqueue_failure", "status_20min", "uptime_wrap",
    "payload_capacity", "status_during_ack", "status_registration_overlap",
    "latest_burst_register", "latest_burst_change", "latest_burst_clear",
    "inflight_register_register", "inflight_register_clear",
    "inflight_clear_register", "inflight_clear_clear",
    "legacy_event_collision", "fixed_event_collision", "status_disconnect", "probe_lifecycle", "probe_stall", "pressure", "capacity", "registration_capacity",
])
def test_production_delivery(delivery_binary, scenario):
    result = subprocess.run([str(delivery_binary), scenario], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_sensor_path_has_no_direct_mqtt_calls():
    source = (PROJECT / "src/sensor_drivers.c").read_text()
    assert "return sen66_delivery_submit(logical_id, &ctx->sample);" in source
    assert "mqtt_registration_publish_sen66" not in source
    mqtt = (PROJECT / "src/mqtt_registration.c").read_text()
    setters = mqtt[mqtt.index("void mqtt_registration_set_sen66_connected"):
                   mqtt.index("void mqtt_registration_flush_sensor_status")]
    assert "esp_mqtt_" not in setters
    assert "publish_registration_status" not in setters


def test_sensor_status_does_not_use_mqtt_internal_event_queue():
    source = (PROJECT / "src/mqtt_registration.c").read_text()
    assert "esp_mqtt_dispatch_custom_event" not in source
    assert "MQTT_USER_EVENT" not in source
    assert "CONFIG_MQTT_EVENT_QUEUE_SIZE" not in source
