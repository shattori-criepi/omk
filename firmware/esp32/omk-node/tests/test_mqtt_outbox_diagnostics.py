"""Exercise OMK diagnostics and the installed ESP-MQTT outbox on the host.

No simulated radio or throughput claim: check byte accounting, expiry, event
routing (including QoS 0 id=0), and the effective poll/configuration constants.
"""
import json
from pathlib import Path
import subprocess

import pytest

PROJECT = Path(__file__).parents[1]
MQTT = PROJECT / "managed_components/espressif__mqtt"


def compile_run(tmp_path, source, includes=()):
    path = tmp_path / "test.c"
    path.write_text(source)
    binary = tmp_path / "test"
    command = ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(tmp_path)]
    for directory in includes:
        command += ["-I", str(directory)]
    subprocess.run(command + [str(path), "-o", str(binary)], check=True, capture_output=True, text=True)
    return subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout


def delivery_source():
    source = (PROJECT / "src/mqtt_registration.c").read_text()
    return source[source.index("/* MQTT delivery diagnostics:"):source.index("static bool logical_id_is_valid(const char *logical_id);")]


def test_diagnostics_sampled_peak_events_and_enqueue_result(tmp_path):
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
typedef int portMUX_TYPE;
typedef int esp_mqtt_event_id_t;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) assert(++*(p)==1)
#define portEXIT_CRITICAL(p) assert(--*(p)==0)
#define MQTT_EVENT_DELETED 10
#define MQTT_EVENT_ERROR 0
static void *client=(void *)1;
static int bytes, result, reads;
static int esp_mqtt_client_get_outbox_size(void *);
static int esp_mqtt_client_enqueue(void *,const char *,const char *,int,int,int,bool);
''' + delivery_source() + r'''
static int esp_mqtt_client_get_outbox_size(void *c) {
    assert(c && mqtt_diagnostic_lock==0); ++reads; return bytes;
}
static int esp_mqtt_client_enqueue(void *c,const char *t,const char *p,int len,int qos,int retain,bool store) {
    assert(c && !strcmp(t,"test") && !strcmp(p,"payload"));
    assert(!len && !qos && !retain && store && mqtt_diagnostic_lock==0);
    return result;
}
int main(void) {
    char payload[256];
    bytes=1200;assert(enqueue_observed("test","payload")==0);
    bytes=4096;assert(enqueue_observed("test","payload")==0);
    bytes=128;result=-2;assert(enqueue_observed("test","payload")==-2);
    assert(mqtt_outbox_sampled_max_bytes==4096 && reads==3);
    record_mqtt_diagnostic_event(MQTT_EVENT_DELETED);
    record_mqtt_diagnostic_event(MQTT_EVENT_DELETED);
    record_mqtt_diagnostic_event(MQTT_EVENT_ERROR);
    record_mqtt_diagnostic_event(99); // Other events, including reconnect, do not reset counts.
    assert(format_mqtt_diagnostics(payload,sizeof(payload)));puts(payload);
    assert(!format_mqtt_diagnostics(payload,8));
    client=NULL;assert(sample_mqtt_outbox()==0); // No API call with an uninitialized client.
    client=(void *)1;bytes=0;assert(format_mqtt_diagnostics(payload,sizeof(payload)));puts(payload);
    return 0;
}
'''
    first, reconnected = map(json.loads, compile_run(tmp_path, source).splitlines())
    assert first == {"outbox_bytes": 128, "outbox_sampled_max_bytes": 4096, "expired_messages": 2, "error_events": 1}
    assert reconnected == {**first, "outbox_bytes": 0}
    source = (PROJECT / "src/mqtt_registration.c").read_text()
    handler = source[source.index("static void mqtt_event_handler"):source.index("uint32_t mqtt_registration_get_disconnect_count")]
    assert "record_mqtt_diagnostic_event((esp_mqtt_event_id_t)event_id);" in handler


def require_mqtt():
    if not (MQTT / "mqtt_client.c").exists():
        pytest.skip("Build atom-s3-lite first to install locked ESP-MQTT sources")


def test_actual_outbox_expiry_reports_every_qos0_message(tmp_path):
    require_mqtt()
    (tmp_path / "sdkconfig.h").write_text("#define CONFIG_MQTT_REPORT_DELETED_MESSAGES 1\n")
    (tmp_path / "esp_err.h").write_text("#include <stdint.h>\n#include <stddef.h>\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n")
    (tmp_path / "esp_heap_caps.h").write_text("#define MALLOC_CAP_DEFAULT 0\n#define heap_caps_malloc(n,caps) malloc(n)\n")
    (tmp_path / "esp_log.h").write_text(r'''
#define ESP_LOGD(tag,...) ((void)(tag))
#define ESP_LOGE(tag,...) ((void)(tag))
#define ESP_MEM_CHECK(tag,p,action) do { (void)(tag); if (!(p)) { action; } } while(0)
''')
    vendor = (MQTT / "mqtt_client.c").read_text()
    deletion = vendor[vendor.index("static void mqtt_delete_expired_messages("):vendor.index("/**\n * @brief When using multiple queued item")]
    outbox = str((MQTT / "lib/mqtt_outbox.c").resolve())
    source = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <sys/queue.h>
#ifndef STAILQ_FOREACH_SAFE
#define STAILQ_FOREACH_SAFE(v,h,f,t) for ((v)=STAILQ_FIRST(h); (v) && ((t)=STAILQ_NEXT(v,f),1); (v)=(t))
#endif
''' + f'#include "{outbox}"\n' + r'''
#define MQTT_EVENT_DELETED 10
typedef struct {outbox_handle_t outbox;struct {int event_id,msg_id;} event;} client_t;
typedef client_t *esp_mqtt_client_handle_t;
static long long now;
static int events, zero_ids;
static long long platform_tick_get_ms(void) {return now;}
static esp_err_t esp_mqtt_dispatch_event(esp_mqtt_client_handle_t c) {
    assert(c->event.event_id==MQTT_EVENT_DELETED);++events;
    if(c->event.msg_id==0)++zero_ids;
    return ESP_OK;
}
''' + deletion + r'''
int main(void) {
    client_t c={.outbox=outbox_init()};assert(c.outbox);
    uint8_t data[128]={0};
    outbox_message_t m={.data=data,.len=sizeof(data),.msg_type=3,.msg_qos=0,.msg_id=0};
    assert(outbox_enqueue(c.outbox,&m,0));
    assert(outbox_enqueue(c.outbox,&m,0));
    m.msg_qos=1;m.msg_id=17;assert(outbox_enqueue(c.outbox,&m,0));
    assert(outbox_get_size(c.outbox)==384);
    now=30000;mqtt_delete_expired_messages(&c);assert(events==0);
    now=30001;mqtt_delete_expired_messages(&c);
    assert(events==3 && zero_ids==2 && outbox_get_size(c.outbox)==0);
    mqtt_delete_expired_messages(&c);assert(events==3); // No phantom event on -1.
    m.msg_qos=0;m.msg_id=0;
    outbox_item_handle_t sent=outbox_enqueue(c.outbox,&m,now);assert(sent);
    assert(outbox_delete_item(c.outbox,sent)==ESP_OK); // Normal send removes QoS 0 silently.
    mqtt_delete_expired_messages(&c);assert(events==3);
    outbox_destroy(c.outbox);return 0;
}
'''
    compile_run(tmp_path, source, [MQTT / "lib/include"])


def test_custom_config_keeps_other_effective_mqtt_defaults(tmp_path):
    require_mqtt()
    # Use the actual Kconfig output for this board (also an explicit build input).
    config = (PROJECT / "sdkconfig.atom-s3-lite").read_text()
    header = []
    for line in config.splitlines():
        if line.startswith("CONFIG_"):
            key, value = line.split("=", 1)
            if value != "n":
                header.append(f"#define {key} {1 if value == 'y' else value}")
    (tmp_path / "sdkconfig.h").write_text("\n".join(header))
    vendor = (MQTT / "mqtt_client.c").read_text()
    poll = vendor[vendor.index("static inline int max_poll_timeout("):vendor.index("static inline void run_event_loop(")]
    compile_run(tmp_path, '''
#include <assert.h>
#include "mqtt_config.h"
typedef void *esp_mqtt_client_handle_t;
#pragma GCC diagnostic ignored "-Wunused-parameter"
''' + poll + '''
int main(void) {
    assert(CONFIG_MQTT_USE_CUSTOM_CONFIG==1 && MQTT_REPORT_DELETED_MESSAGES==1);
    assert(MQTT_POLL_READ_TIMEOUT_MS==100);
    assert(max_poll_timeout(0,MQTT_POLL_READ_TIMEOUT_MS)==100);
    assert(MQTT_EVENT_QUEUE_SIZE==1 && OUTBOX_EXPIRED_TIMEOUT_MS==30000);
    assert(MQTT_BUFFER_SIZE_BYTE==1024 && MQTT_TASK_STACK==6144 && MQTT_TASK_PRIORITY==5);
    assert(MQTT_TCP_DEFAULT_PORT==1883 && MQTT_SSL_DEFAULT_PORT==8883);
    assert(MQTT_WS_DEFAULT_PORT==80 && MQTT_WSS_DEFAULT_PORT==443);
    assert(MQTT_RECON_DEFAULT_MS==10000 && MQTT_KEEPALIVE_TICK==120 && MQTT_NETWORK_TIMEOUT_MS==10000);
#ifdef MQTT_DISABLE_API_LOCKS
    assert(!MQTT_DISABLE_API_LOCKS);
#endif
    return 0;
}
''', [MQTT / "lib/include"])


def test_generated_atom_build_uses_managed_mqtt_and_poll_100ms():
    build = PROJECT / ".pio/build/atom-s3-lite"
    if not (build / "config/sdkconfig.json").exists():
        pytest.skip("Run the AtomS3 Lite build to check effective generated config")
    config = json.loads((build / "config/sdkconfig.json").read_text())
    assert config["MQTT_POLL_READ_TIMEOUT_MS"] == 100
    assert config["MQTT_USE_CUSTOM_CONFIG"] and config["MQTT_REPORT_DELETED_MESSAGES"]
    assert config["MQTT_OUTBOX_EXPIRED_TIMEOUT_MS"] == 30000
    assert config["MQTT_EVENT_QUEUE_SIZE"] == 1
    assert not config["MQTT_DISABLE_API_LOCKS"]
    entries = json.loads((build / "compile_commands.json").read_text())
    entry = next(e for e in entries if e["file"].endswith("espressif__mqtt/mqtt_client.c"))
    assert str((build / "config").resolve()) in entry["command"]


def test_node_status_preserves_fields_capacity_and_allocation_fallback(tmp_path):
    mqtt = (PROJECT / "src/mqtt_registration.c").read_text()
    mesh = mqtt[mqtt.index("static esp_err_t publish_mesh_diagnostic("):mqtt.index("static void start_or_reconnect_mqtt(")]
    constants = "\n".join(line for line in mqtt.splitlines() if line.startswith(("#define OMK_MQTT_TOPIC_SIZE ", "#define OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE ")))
    output = compile_run(tmp_path, r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int portMUX_TYPE;
typedef int esp_mqtt_event_id_t;
typedef int esp_err_t;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) assert(++*(p)==1)
#define portEXIT_CRITICAL(p) assert(--*(p)==0)
#define MQTT_EVENT_DELETED 10
#define MQTT_EVENT_ERROR 0
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_SIZE 3
static void *client=(void *)1;
static bool client_connected=true, fail_allocation;
static unsigned calls;
static char captured[2048];
static int esp_mqtt_client_get_outbox_size(void *c) {assert(c);return 1234;}
static int esp_mqtt_client_enqueue(void *c,const char *t,const char *p,int len,int qos,int retain,bool store) {
    assert(c && !strcmp(t,"omk/node/09dda0d5a8f2/status"));
    assert(!len && !qos && !retain && store);
    ++calls;assert(strlen(p)<sizeof(captured));strcpy(captured,p);return 0;
}
static esp_err_t node_identity_get_id(uint64_t *id) {*id=0x09dda0d5a8f2;return ESP_OK;}
static void *test_malloc(size_t size) {return fail_allocation?NULL:malloc(size);}
#define malloc test_malloc
''' + constants + "\n" + delivery_source() + mesh + r'''
int main(void) {
    record_mqtt_diagnostic_event(MQTT_EVENT_DELETED);
    assert(mqtt_registration_publish_mesh_status("{\"node_id\":\"09dda0d5a8f2\",\"is_root\":true}")==ESP_OK);
    assert(calls==1);puts(captured);
    mqtt_outbox_sampled_max_bytes=UINT32_MAX;
    mqtt_expired_messages=UINT32_MAX;mqtt_error_events=UINT32_MAX;
    char original[1152];memset(original,' ',sizeof(original));
    memcpy(original,"{\"node_id\":0",12);original[1150]='}';original[1151]=0;
    assert(mqtt_registration_publish_mesh_status(original)==ESP_OK);
    assert(calls==2);puts(captured);
    fail_allocation=true;
    assert(mqtt_registration_publish_mesh_status(original)==ESP_OK);
    assert(calls==3 && !strcmp(captured,original));
    client_connected=false;
    assert(mqtt_registration_publish_mesh_status(original)==ESP_ERR_INVALID_STATE);
    assert(calls==3);
    return 0;
}
''')
    status, maximum = map(json.loads, output.splitlines())
    assert status["node_id"] == "09dda0d5a8f2" and status["is_root"] is True
    assert status["mqtt"] == {"outbox_bytes": 1234, "outbox_sampled_max_bytes": 1234, "expired_messages": 1, "error_events": 0}
    assert maximum["mqtt"]["outbox_sampled_max_bytes"] == 2**32 - 1
    assert maximum["mqtt"]["expired_messages"] == maximum["mqtt"]["error_events"] == 2**32 - 1
