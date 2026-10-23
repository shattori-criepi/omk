"""Run production diagnostics and MQTT builders with deterministic time/outbox."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest

SOURCE = Path(__file__).parents[1] / "src"
ROOT = SOURCE.parents[3]


@pytest.fixture(scope="module")
def diagnostic_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sen66-diagnostics")
    headers = {
        "esp_err.h": """
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_SIZE 3
#define ESP_ERR_TIMEOUT 4
#define ESP_ERR_NO_MEM 5
""",
        "esp_random.h": "#include <stddef.h>\nvoid esp_fill_random(void *, size_t);\n",
        "esp_timer.h": "#include <stdint.h>\nint64_t esp_timer_get_time(void);\n",
        "freertos/FreeRTOS.h": """
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) assert(++*(p) == 1)
#define portEXIT_CRITICAL(p) assert(--*(p) == 0)
""",
    }
    for name, content in headers.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    mqtt = (SOURCE / "mqtt_registration.c").read_text()
    constants = "\n".join(re.findall(r"^#define OMK_MQTT_(?:TOPIC|SEN66_PAYLOAD|MESH_STATUS_PAYLOAD)_SIZE .+$", mqtt, re.M))
    measurement = (SOURCE / "sen66_sensor.h").read_text().split("typedef struct sen66_measurement", 1)[1].split("} sen66_measurement_t;", 1)[0]
    delivery = mqtt[mqtt.index("/* MQTT delivery diagnostics:"):mqtt.index("static bool logical_id_is_valid(const char *logical_id);")]
    builders = mqtt[mqtt.index("static bool append_text("):]
    mesh = mqtt[mqtt.index("static esp_err_t publish_mesh_diagnostic("):mqtt.index("static void start_or_reconnect_mqtt(void) {")]
    harness = r'''
#include <assert.h>
#include <inttypes.h>
#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sen66_diagnostics.c"
#include "communication_limits.h"
#define OMK_NETWORK_DIAGNOSTICS 1
#include "network_diagnostics.h"
nd_rejections_t network_diag_rejections(void) {
    nd_rejections_t r;for(unsigned i=0;i<ND_REJECTIONS;i++)r.count[i]=UINT32_MAX;return r;
}
static bool mesh_netif_allow_data(void) { return true; }
''' + constants + "\ntypedef struct sen66_measurement" + measurement + "} sen66_measurement_t;\n" + r'''
typedef int esp_mqtt_event_id_t;
#define MQTT_EVENT_DELETED 10
#define MQTT_EVENT_ERROR 0
static void *client = (void *)1;
static int esp_mqtt_client_get_outbox_size_nowait(void *c) { assert(c); return 0; }
static bool client_connected = true, fail_allocation;
static uint64_t now_ms;
static unsigned random_calls, enqueue_calls;
static int enqueue_return;
static char captured[2816];
enum {SEN66_TX_ENQUEUE, SEN66_TX_OUTBOX, SEN66_TX_IDLE};
static void sen66_delivery_phase(int p, uint32_t seq) {(void)p;(void)seq;}
static bool sen66_delivery_format(char *buffer,size_t size) {
    uint32_t s=UINT32_MAX,r=s,i=s,e=s,u=s,seq=s;
    unsigned p=3;bool waiting=false;
    uint64_t age=UINT64_MAX,enqueue=age,outbox=age,status_age=age,status_max=age;
    int n = snprintf(buffer, size,
        "{\"submitted\":%" PRIu32 ",\"replaced\":%" PRIu32
        ",\"invalidated\":%" PRIu32 ",\"expired\":%" PRIu32
        ",\"unavailable\":%" PRIu32 ",\"pending\":%s,\"phase\":%u"
        ",\"active_seq\":%" PRIu32 ",\"phase_age_ms\":%" PRIu64
        ",\"max_enqueue_ms\":%" PRIu64 ",\"max_outbox_ms\":%" PRIu64
        ",\"status_age_ms\":%" PRIu64 ",\"max_status_ms\":%" PRIu64 "}",
        s, r, i, e, u, waiting ? "true" : "false", p, seq, p ? age : 0, enqueue, outbox,
        status_age, status_max);
    return n >= 0 && (size_t)n < size;
}
static void *test_malloc(size_t n) { return fail_allocation ? NULL : malloc(n); }
#define malloc test_malloc
void esp_fill_random(void *b, size_t n) { memset(b, ++random_calls, n); }
int64_t esp_timer_get_time(void) { return now_ms * 1000; }
static esp_err_t node_identity_get_id(uint64_t *id) { *id = 0x09dda0d5a8f2; return ESP_OK; }
static int esp_mqtt_client_enqueue_bounded(void *c, const char *topic, const char *payload,
                                   int len, int qos, int retain, unsigned limit, void (*submitted)(int)) {
    assert(c && topic && !len && !qos && !retain && limit==OMK_MQTT_DATA_BYTES && !submitted);
    assert(strlen(payload) < sizeof(captured));
    strcpy(captured, payload);
    ++enqueue_calls;
    now_ms += 7;
    return enqueue_return;
}
''' + delivery + mesh + builders + r'''
static sen66_measurement_t sample = {.temperature_celsius=25.5, .nox_index=NAN};
static void successful_read(void) {
    now_ms += 10000;
    sen66_diagnostics_read_begin(now_ms);
    now_ms += 60;
    sample.sequence = sen66_diagnostics_read_end(ESP_OK, true, 60);
    sample.uptime_ms = now_ms;
}
static void accepted(void) {
    assert(mqtt_registration_publish_sen66("sen66-001", &sample) == ESP_OK);
    puts(captured);
}
int main(void) {
    char diagnostic[1024];
    record_mqtt_diagnostic_event(MQTT_EVENT_DELETED);
    assert(!sen66_diagnostics_format(diagnostic, sizeof(diagnostic)));
    assert(mqtt_registration_publish_mesh_status("{\"node_id\":\"09dda0d5a8f2\"}") == ESP_OK);
    assert(!strstr(captured, "sen66"));
    assert(strstr(captured, "\"mqtt\":"));
    successful_read(); accepted(); // seq=1, enqueue_seq=1, return zero is success
    uint64_t boot = counters.boot_id;
    successful_read(); enqueue_return=-1;
    assert(mqtt_registration_publish_sen66("sen66-001", &sample) == ESP_FAIL);
    successful_read(); enqueue_return=-2;
    assert(mqtt_registration_publish_sen66("sen66-001", &sample) == ESP_ERR_NO_MEM);
    assert(counters.enqueue_failures==2 && counters.last_failed_seq==3);
    successful_read(); client_connected=false;
    unsigned calls=enqueue_calls;
    assert(mqtt_registration_publish_sen66("sen66-001", &sample) == ESP_ERR_INVALID_STATE);
    assert(enqueue_calls==calls && counters.publish_unavailable==1);
    client_connected=true;
    successful_read(); // seq=5 has no Logical ID: no publish at all
    successful_read(); enqueue_return=0; accepted(); // seq=6, enqueue_seq=2
    successful_read(); accepted(); // seq=7, enqueue_seq=3: simulate lost after enqueue
    successful_read(); enqueue_return=42; accepted(); // seq=8, enqueue_seq=4
    assert(counters.read_attempts==8 && counters.read_success==8);
    assert(counters.publish_attempts==7 && counters.publish_failures==3);
    assert(counters.enqueue_success==4 && counters.enqueue_failures==2);
    assert(counters.last_enqueue_seq==8 && counters.last_failed_seq==4);
    assert(counters.max_publish_ms==7 && counters.max_read_ms==60);
    assert(counters.max_interval_ms==10067 && counters.boot_id==boot && random_calls==1);
    assert(mqtt_registration_publish_mesh_status("{\"node_id\":\"09dda0d5a8f2\",\"is_root\":true}") == ESP_OK);
    puts(captured); // counters exclude diagnostic enqueue
    assert(counters.enqueue_success==4);
    fail_allocation=true;
    assert(mqtt_registration_publish_mesh_status("{\"is_root\":true}") == ESP_OK);
    assert(!strcmp(captured,"{\"is_root\":true}"));
    fail_allocation=false;
    // Upper bounds fit, truncation fails, metadata never changes null handling.
    counters = (sen66_diagnostics_t){.boot_id=UINT64_MAX,
        .read_attempts=UINT32_MAX,.read_success=UINT32_MAX,.not_ready=UINT32_MAX,.read_errors=UINT32_MAX,
        .publish_attempts=UINT32_MAX,.publish_failures=UINT32_MAX,.publish_unavailable=UINT32_MAX,
        .enqueue_success=UINT32_MAX,.enqueue_failures=UINT32_MAX,.last_enqueue_seq=UINT32_MAX,.last_failed_seq=UINT32_MAX,
        .last_read_error=INT32_MIN,.last_read_ms=UINT64_MAX,.last_interval_ms=UINT64_MAX,
        .max_interval_ms=UINT64_MAX,.max_read_ms=UINT64_MAX,.max_publish_ms=UINT64_MAX};
    mqtt_outbox_sampled_max_bytes=UINT32_MAX;
    mqtt_expired_messages=UINT32_MAX;
    mqtt_error_events=UINT32_MAX;
    assert(sen66_diagnostics_format(diagnostic, sizeof(diagnostic)));
    assert(!sen66_diagnostics_format(diagnostic, 8));
    char original[1152];
    memset(original,' ',sizeof(original)); memcpy(original,"{\"node_id\":0",12); original[1150]='}'; original[1151]=0;
    assert(mqtt_registration_publish_mesh_status(original)==ESP_OK);
    puts(captured);
    sample.sequence=UINT32_MAX;sample.uptime_ms=UINT32_MAX;
    char long_id[49];memset(long_id,'a',48);long_id[48]=0;
    assert(mqtt_registration_publish_sen66(long_id,&sample)==ESP_OK);
    puts(captured); // enqueue ordinal wraps modulo 2^32
    // MCU reboot: BSS resets, new random boot identity, sequences restart at 1.
    memset(&counters,0,sizeof(counters)); now_ms=0;
    successful_read(); accepted();
    assert(counters.boot_id!=boot && sample.sequence==1 && counters.enqueue_success==1);
    return 0;
}
'''
    path = tmp / "harness.c"
    path.write_text(harness)
    exe = tmp / "diagnostics"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(tmp), "-I", str(SOURCE), str(path), "-o", str(exe)], check=True, capture_output=True, text=True)
    result = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_counters_enqueue_failures_and_restart(diagnostic_run):
    first, resumed, lost, following, status, maximum, wrap, reboot = diagnostic_run
    assert [m["meta"]["seq"] for m in (first, resumed, lost, following)] == [1, 6, 7, 8]
    assert [m["meta"]["enqueue_seq"] for m in (first, resumed, lost, following)] == [1, 2, 3, 4]
    assert status["sen66"]["read_success"] == 8
    assert status["sen66"]["enqueue_success"] == 4
    assert status["sen66"]["publish_failures"] == 3
    assert status["is_root"] is True
    assert maximum["sen66"]["max_read_ms"] == 2**64 - 1
    assert maximum["mqtt"]["expired_messages"] == 2**32 - 1
    assert maximum["admission"] == [2**32 - 1] * 6
    assert maximum["sen66_tx"]["submitted"] == 2**32 - 1
    assert wrap["meta"]["enqueue_seq"] == 0
    assert reboot["meta"]["boot_id"] != first["meta"]["boot_id"]
    assert reboot["meta"]["seq"] == reboot["meta"]["enqueue_seq"] == 1
    assert first["nox_index"] is None and first["temperature_celsius"] == 25.5


def test_gap_separates_before_and_after_enqueue(diagnostic_run):
    first, resumed, _lost, following, *_ = diagnostic_run
    # Five measurements between observed seq 1 and 6, but only one enqueue:
    # all four missing measurements were lost before successful enqueue.
    def gaps(a, b):
        assert a["meta"]["boot_id"] == b["meta"]["boot_id"]
        ds = (b["meta"]["seq"] - a["meta"]["seq"]) % 2**32
        de = (b["meta"]["enqueue_seq"] - a["meta"]["enqueue_seq"]) % 2**32
        return ds - de, de - 1
    assert gaps(first, resumed) == (4, 0)
    assert gaps(resumed, following) == (0, 1)


def test_measurement_metadata_does_not_add_dashboard_items(diagnostic_run, tmp_path):
    path = ROOT / "services/sensor-collector/src/latest_store.py"
    spec = importlib.util.spec_from_file_location("sen66_latest_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = diagnostic_run[0]
    store = module.GenericLatestStore(tmp_path)
    assert store.write({"received_at": "2026-10-08T12:00:00+09:00", "topic": "omk/sen66-001/sen66", "payload": first})
    fields = {item["field"] for item in json.loads((tmp_path / "catalog.json").read_text())["items"]}
    assert "meta" not in fields and "temperature_celsius" in fields


def test_existing_transformer_normalization_ignores_metadata(diagnostic_run):
    # Execute the actual pure normalization functions without requiring the
    # optional PyArrow writer on a firmware test host.
    import ast
    import math
    from datetime import datetime
    from typing import Any
    from zoneinfo import ZoneInfo

    path = ROOT / "services/data-transformer/src/data_transformer/transformer.py"
    tree = ast.parse(path.read_text())
    names = {"RecordError", "_timestamp", "_number", "_payload", "_common", "_sen66", "_is_status_topic"}
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    scope = {"Any": Any, "datetime": datetime, "Path": Path, "math": math,
             "JST": ZoneInfo("Asia/Tokyo"), "SEN66": "sen66"}
    exec(compile(tree, str(path), "exec"), scope)
    payload = diagnostic_run[0]
    record = {"received_at": "2026-10-08T12:00:00+09:00", "topic": "omk/sen66-001/sen66", "payload": payload}
    before = scope["_sen66"]({**record, "payload": {k: v for k, v in payload.items() if k != "meta"}}, Path("raw.jsonl"), 1)
    assert scope["_sen66"](record, Path("raw.jsonl"), 1) == before
    assert scope["_is_status_topic"]({"topic": "omk/node/09dda0d5a8f2/status", "payload": diagnostic_run[4]})
