"""Run the production native record formatter with real cJSON and host RTOS doubles."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
CJSON_LIMIT = next(line.split("=", 1)[1] for line in (BASE / "sdkconfig.atom-s3-lite").read_text().splitlines() if line.startswith("CONFIG_CJSON_NESTING_LIMIT="))
SOURCE = BASE / "src"


@pytest.fixture(scope="module")
def messages_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("node-messages")
    compiler = shutil.which("cc")
    assert compiler, "host C compiler is required"
    cjson = BASE / "managed_components/espressif__cjson/cJSON"
    if not cjson.exists():
        cjson = Path(os.environ["OMK_TEST_CJSON_DIR"])
    for name in ("ble_scan_runtime.h", "ble_scan_trial.h", "ble_scan_metrics.h", "discovery_ble.h", "node_messages.c", "node_messages.h", "mesh_diag.h", "sensor_drivers.c", "sensor_driver.h",
                 "sen66_sensor.h", "node_registration.h", "node_logical_id.h", "mesh_transport.h", "mesh_wire.h", "node_protocol.h"):
        shutil.copyfile(SOURCE / name, work / name)
    headers = {
        "esp_err.h": '''#pragma once
        typedef int esp_err_t;
        #define ESP_OK 0
        #define ESP_FAIL -1
        #define ESP_ERR_NO_MEM 0x101
        #define ESP_ERR_INVALID_ARG 0x102
        #define ESP_ERR_INVALID_STATE 0x103
        #define ESP_ERR_INVALID_SIZE 0x104
        const char *esp_err_to_name(int);
        ''',
        "esp_log.h": '#define ESP_LOGW(...) ((void)0)\n',
        "esp_random.h": '#include <stdint.h>\nuint32_t esp_random(void);\n',
        "esp_system.h": '#pragma once\ntypedef enum { ESP_RST_POWERON=1, ESP_RST_TASK_WDT=6, ESP_RST_CPU_LOCKUP=15 } esp_reset_reason_t;\nesp_reset_reason_t esp_reset_reason(void);\n',
        "esp_timer.h": '#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
        "node_identity.h": '#include <stddef.h>\nint node_identity_get_id_hex(char *, size_t);\n',
        "driver/i2c_master.h": '#pragma once\ntypedef void *i2c_master_dev_handle_t;\ntypedef void *i2c_master_bus_handle_t;\n',
        "freertos/FreeRTOS.h": '''#pragma once
        #include <stdint.h>
        #define pdTRUE 1
        #define pdPASS 1
        #define pdMS_TO_TICKS(v) (v)
        #define portMAX_DELAY UINT32_MAX
        typedef int portMUX_TYPE;
        #define portMUX_INITIALIZER_UNLOCKED 0
        #define portENTER_CRITICAL(p) ((void)(p))
        #define portEXIT_CRITICAL(p) ((void)(p))
        ''',
        "freertos/semphr.h": '''#pragma once
        #include <pthread.h>
        typedef pthread_mutex_t StaticSemaphore_t;
        typedef pthread_mutex_t *SemaphoreHandle_t;
        static inline SemaphoreHandle_t xSemaphoreCreateMutexStatic(StaticSemaphore_t *p) { pthread_mutex_init(p, 0); return p; }
        static inline int xSemaphoreTake(SemaphoreHandle_t p, unsigned ticks) { return (ticks ? pthread_mutex_lock(p) : pthread_mutex_trylock(p)) == 0; }
        static inline int xSemaphoreGive(SemaphoreHandle_t p) { return pthread_mutex_unlock(p) == 0; }
        ''',
        "freertos/task.h": '''#pragma once
        typedef void *TaskHandle_t;
        int xTaskCreate(void (*fn)(void *), const char *, unsigned, void *, unsigned, TaskHandle_t *);
        unsigned ulTaskNotifyTake(int, unsigned);
        void xTaskNotifyGive(TaskHandle_t);
        ''',
    }
    for name, contents in headers.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    shutil.copyfile(BASE / "tests/node_messages_host.c", work / "harness.c")
    executable = work / "messages"
    subprocess.run([compiler, "-std=c11", "-DCJSON_NESTING_LIMIT=" + CJSON_LIMIT, "-pthread", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                    "-I", str(work), "-I", str(cjson), str(work / "harness.c"),
                    str(work / "sensor_drivers.c"),
                    *[str(BASE/"src"/n) for n in ("ble_scan_trial.c","ble_scan_command.c","ble_scan_metrics.c","ble_scan_runtime.c")], str(cjson / "cJSON.c"), "-lm", "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    return executable


def run(messages_host, mode):
    return subprocess.run([str(messages_host), mode], check=True, capture_output=True, text=True).stdout


def test_deep_config_rejected_before_application_and_normal_replays_still_work(messages_host):
    run(messages_host, "review_depth")


def test_exact_native_schema_and_gateway_ingestion(messages_host):
    records = [json.loads(line) for line in run(messages_host, "schema").splitlines()]
    state, registration, sample, ble = records
    assert state["node_id"] == registration["node_id"] == sample["node_id"] == ble["node_id"] == "020000000041"
    assert all(value["protocol_version"] == 2 and value["revision"] == 7 for value in records)
    assert all(len(value["boot_id"]) == 16 for value in records)
    assert state["registration_state"] == "registered" and state["logical_id"] == "sen66-001"
    assert state["connected_sensors"] == ["sen66"] and state["capabilities"] == 3
    assert state["sen66_rc"] == 12 and state["sen66_to"] == 3
    assert state["mesh_layer"] == 3 and state["is_root"] is False
    assert state["parent_bssid"] == "02:00:00:00:00:01"
    assert state["root_mac"] == "02:00:00:00:00:02"
    assert state["mqtt_connected"] is False
    assert state["reset_reason"] == 6
    assert state["last_disconnect_rssi"] is None
    assert "parent_rssi" not in state  # No current RSSI is sampled or fabricated.
    assert all("mesh_layer" not in value for value in (registration, sample, ble))
    assert sample["device_id"] == "sen66-001" and sample["seq"] == 42 and sample["uptime_ms"] == 1234
    assert sample["meta"] == {"boot_id": sample["boot_id"], "seq": 42, "enqueue_seq": 1}
    assert sample["pm1_0_ug_m3"] is None and sample["nox_index"] is None
    assert sample["temperature_celsius"] == 23.5 and sample["co2_ppm"] == 600
    assert ble["relay_node_id"] == ble["node_id"] and ble["ble_address"] == "020000000004"
    assert ble["manufacturer_data"] == [{"company_id": 2409, "data": "020000000004f40304992c"}]
    assert ble["service_data"] == [{"uuid": "0000fd3d-0000-1000-8000-00805f9b34fb", "data": "540064"}]
    # Import the actual Gateway validator when its dependencies are provided.
    from omk_ble.service import BleManager
    parsed = BleManager._parse_relay_observation(ble, ble["node_id"])
    assert parsed is not None and parsed[0] == "020000000004"


@pytest.mark.parametrize("mode", ["allocations", "invalid", "config", "nonblocking", "driver", "limits", "start", "invalid_registration", "message_errors"])
def test_native_formatter_failure_and_control_contracts(messages_host, mode):
    run(messages_host, mode)


def test_diagnostics_fit_payload_at_maximum_values(messages_host):
    lines = run(messages_host, "diagnostic_limits").splitlines()
    state, registration = map(json.loads, lines)
    assert len(lines[0].encode()) <= 900
    assert len(state["logical_id"]) == 48
    assert state["seq"] == state["revision"] == 0xffffffff
    for key in ("parent_disconnect_count", "root_change_count", "tx_dropped",
                "mesh_tx_ok", "mesh_tx_errors", "mesh_rx_ok"):
        assert state[key] == 0xffffffff
    assert state["last_disconnect_rssi"] == -128
    assert state["last_disconnect_reason"] == 255
    assert state["reset_reason"] == 15
    assert state["recovery"] == {
        "action": 255, "req": 65535, "ok": 65535, "err": -2147483648,
        "link": 65535, "ack": 65535, "vote": 65535, "demote": 65535,
    }
    assert len(registration) == 12 and "reset_reason" not in registration


def test_unknown_mac_and_disconnect_history_are_null(messages_host):
    state, registration = map(json.loads, run(messages_host, "diagnostic_unknown").splitlines())
    assert all(state[key] is None for key in
               ("parent_bssid", "root_mac", "last_disconnect_reason", "last_disconnect_rssi"))
    assert state["mesh_layer"] == -1
    assert len(registration) == 12


def test_diagnostic_heartbeat_preserves_gateway_revision_and_freshness(messages_host, tmp_path):
    from types import SimpleNamespace
    from omk_ble.node_registry import NodeRegistry
    from omk_ble.registry import SensorRegistry
    from omk_ble.service import BleManager

    state, registration, _, _ = map(json.loads, run(messages_host, "schema").splitlines())
    publisher = SimpleNamespace(is_connected=lambda: True,
        publish=lambda *a, **k: SimpleNamespace(rc=0, is_published=lambda: True))
    registry = NodeRegistry(tmp_path / "nodes.json")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher, node_registry=registry)
    node = state["node_id"]
    manager.handle_node_mqtt(f"omk/node/{node}/registration/status",
                            json.dumps(registration).encode(), retain=True)
    assert not manager.node_list()[0]["online"]
    manager.handle_node_mqtt(f"omk/node/{node}/status", json.dumps(state).encode())
    assert manager.node_list()[0]["online"]
    assert registry.list()[node]["revision"] == 7
    manager.request_node_registration(node, "sen66-new")
    assert registry.list()[node]["desired_revision"] == 8
    # A fresh diagnostic snapshot of the old applied state must not confirm
    # the new intent, even with apparently healthy topology flags.
    state["seq"] += 10
    manager.handle_node_mqtt(f"omk/node/{node}/status", json.dumps(state).encode())
    saved = registry.list()[node]
    assert saved["desired_revision"] == 8 and saved["revision"] == 7
    assert saved["request_state"] == "request_sent"


def test_maximum_nullable_reason_can_be_longer_than_number(messages_host):
    lines = run(messages_host, "diagnostic_limits_null").splitlines()
    assert len(lines[0].encode()) == 900
    assert json.loads(lines[0])["last_disconnect_reason"] is None


@pytest.mark.parametrize("mode,expected,fields", [
    ("registration_limits", 329, 12),
    ("registration_limits_ack", 349, 13),
])
def test_registration_payload_maxima_independently(messages_host, mode, expected, fields):
    line = run(messages_host, mode).strip()
    record = json.loads(line)
    assert len(line.encode()) == expected
    assert len(record) == fields
    assert record["seq"] == record["revision"] == 0xffffffff
    assert len(record["logical_id"]) == 48
    if mode.endswith("_ack"):
        assert record["result"] == "rejected"

@pytest.mark.parametrize("mode",["cadence_quiet","cadence_dirty"])
def test_scan_notifications_do_not_shift_status_clock(messages_host,mode):
    run(messages_host,mode)
