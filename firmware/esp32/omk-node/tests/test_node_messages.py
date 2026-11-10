"""Run the production native record formatter with real cJSON and host RTOS doubles."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "src"


@pytest.fixture(scope="module")
def messages_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("node-messages")
    compiler = shutil.which("cc")
    assert compiler, "host C compiler is required"
    cjson = BASE / "managed_components/espressif__cjson/cJSON"
    if not cjson.exists():
        cjson = Path(os.environ["OMK_TEST_CJSON_DIR"])
    for name in ("node_messages.c", "node_messages.h", "sensor_drivers.c", "sensor_driver.h",
                 "sen66_sensor.h", "node_registration.h", "mesh_transport.h", "mesh_wire.h", "node_protocol.h"):
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
    subprocess.run([compiler, "-std=c11", "-pthread", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                    "-I", str(work), "-I", str(cjson), str(work / "harness.c"),
                    str(work / "sensor_drivers.c"), str(cjson / "cJSON.c"), "-lm", "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    return executable


def run(messages_host, mode):
    return subprocess.run([str(messages_host), mode], check=True, capture_output=True, text=True).stdout


def test_exact_native_schema_and_gateway_ingestion(messages_host):
    records = [json.loads(line) for line in run(messages_host, "schema").splitlines()]
    state, registration, sample, ble = records
    assert state["node_id"] == registration["node_id"] == sample["node_id"] == ble["node_id"] == "020000000041"
    assert all(value["protocol_version"] == 2 and value["revision"] == 7 for value in records)
    assert all(len(value["boot_id"]) == 16 for value in records)
    assert state["registration_state"] == "registered" and state["logical_id"] == "sen66-001"
    assert state["connected_sensors"] == ["sen66"] and state["capabilities"] == 3
    assert state["sen66_rc"] == 12 and state["sen66_to"] == 3
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


@pytest.mark.parametrize("mode", ["allocations", "invalid", "config", "nonblocking", "driver", "limits", "start"])
def test_native_formatter_failure_and_control_contracts(messages_host, mode):
    run(messages_host, mode)
