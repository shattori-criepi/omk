"""Actual USB formatter plus RAM getters, with no network/I2C implementations."""
import json
from pathlib import Path
import subprocess

PROJECT = Path(__file__).parents[1]
SOURCE = PROJECT / "src"
HOST = Path(__file__).parent / "network_diag_host"


def function(file, signature):
    text = (SOURCE / file).read_text()
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


def test_usb_runtime_relay_and_maximum_counters(tmp_path):
    for name in ("esp_timer.h", "esp_app_desc.h", "esp_attr.h", "esp_random.h",
                 "esp_err.h", "esp_system.h", "freertos/FreeRTOS.h", "freertos/task.h",
                 "nvs.h", "nvs_flash.h", "driver/usb_serial_jtag.h"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "stubs.h"\n')
    (tmp_path / "driver/i2c_master.h").write_text(
        '#pragma once\ntypedef void *i2c_master_dev_handle_t;\ntypedef void *i2c_master_bus_handle_t;\n')
    runtime = r'''
#include <stdbool.h>
#include "stubs.h"
#include "sen66_delivery.h"
#include "sen66_diagnostics.c"
#include "mesh_liveness.c"
static unsigned esp_get_free_heap_size(void) {return UINT32_MAX;}
static unsigned esp_get_minimum_free_heap_size(void) {return UINT32_MAX;}
static portMUX_TYPE sensor_status_lock=portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE progress_lock=portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE delivery_lock=portMUX_INITIALIZER_UNLOCKED;
static uint32_t sen66_recovery_count,sen66_measurement_timeout_count;
static omk_mqtt_progress_t mqtt_progress;
static uint32_t submitted,replaced,invalidated,expired,unavailable,active_seq;
static sen66_tx_phase_t phase;
static uint64_t phase_since_ms;
static bool occupied;
static uint64_t now_ms(void) {return esp_timer_get_time()/1000;}
'''
    runtime += function("mqtt_registration.c", "void mqtt_registration_snapshot_sensor(")
    runtime += function("mqtt_registration.c", "omk_mqtt_progress_t mqtt_registration_snapshot_progress(")
    runtime += function("sen66_delivery.c", "sen66_delivery_snapshot_t sen66_delivery_snapshot(")
    runtime += '#include "diagnostic_runtime.c"\n' + r'''
void runtime_max(void) {
    memset(&counters,255,sizeof(counters));
    sen66_recovery_count=sen66_measurement_timeout_count=UINT32_MAX;
    submitted=replaced=invalidated=expired=unavailable=active_seq=UINT32_MAX;
    phase=SEN66_TX_OUTBOX;occupied=true;fake_us=UINT64_MAX-100;
    mqtt_progress=(omk_mqtt_progress_t){.ack_count=UINT32_MAX,.ack_ms=UINT32_MAX,
        .pending_id=INT32_MAX,.submitting=true};
}
'''
    (tmp_path / "runtime.c").write_text(runtime)
    (tmp_path / "main.c").write_text(r'''
#include <stdio.h>
#include "network_diagnostics.c"
#include "network_diagnostics_store.c"
void runtime_max(void);
int main(void) {
    network_diag_start();unsigned writes=flash_writes;
    for(unsigned pass=0;pass<2;pass++) {
        if(pass) {runtime_max();state.progress.recovery_flags=0x17f;
            for(unsigned i=0;i<ND_REJECTIONS;i++) rejections.count[i]=UINT32_MAX;}
        for(unsigned p=27;p<=32;p++) {
            char request[40];snprintf(request,sizeof(request),"OMKDIAG 0 %u",p);
            usb_output[0]=0;assert(network_diag_usb(request,0xffffffffffffULL));
            assert(usb_output[0] && strlen(usb_output)<240);puts(usb_output);
        }
    }
    assert(flash_writes==writes && !usb_dropped);
    usb_output[0]=0;assert(network_diag_usb("OMKDIAG 1 27",1) && !usb_output[0]);
}
''')
    binary = tmp_path / "runtime"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-Wno-unused-variable", "-pthread",
                    "-DOMK_NETWORK_DIAGNOSTICS=1", "-I", str(tmp_path), "-I", str(HOST),
                    "-I", str(SOURCE), str(tmp_path / "main.c"), str(tmp_path / "runtime.c"),
                    "-o", str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=5)
    records = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert len(records) == 12
    assert records[0]["read"] == [0] * 7  # no sensor, no initialization side effects
    assert records[1]["mailbox"] == [0] * 9
    assert records[2]["probe"] == [0] * 5
    assert records[6]["read"] == [2**32-1]*4 + [2**64-1] + [2**32-1]*2
    assert records[7]["mailbox"][5:8] == [1, 3, 2**32-1]
    assert records[8]["probe"][:4] == [2**32-1, 2**32-1, 2**31-1, 1]
    assert records[10]["state"][0] == 0x17f
    assert records[11]["admission"] == [2**32-1]*6
