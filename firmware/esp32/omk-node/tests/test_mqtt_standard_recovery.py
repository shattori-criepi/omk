"""Run pinned ESP-MQTT keepalive/abort/reconnect code with transport/time stubs.

A responsive PING path is deliberately not treated as proof of PUBACK progress.
"""
from pathlib import Path
import subprocess

PROJECT = Path(__file__).parents[1]


def test_vendor_keepalive_and_reconnect_boundaries(tmp_path):
    source = (PROJECT / "managed_components/espressif__mqtt/mqtt_client.c").read_text()
    keepalive = source[source.index("static inline bool has_timed_out("):
                       source.index("static inline esp_err_t esp_mqtt_write(")]
    start = source.index("static void esp_mqtt_abort_connection(esp_mqtt_client_handle_t client)\n{")
    abort = source[start:source.index("static bool create_client_data(", start)]
    start = source.index("        case MQTT_STATE_WAIT_RECONNECT:")
    reconnect = source[start:source.index("        default:", start)]
    program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define MQTT_STATE_INIT 0
#define MQTT_STATE_CONNECTED 1
#define MQTT_STATE_WAIT_RECONNECT 2
#define MQTT_EVENT_DISCONNECTED 3
#define RECONNECT_BIT 1
#define MQTT_RECON_DEFAULT_MS 10000
#define portTICK_PERIOD_MS 1
#define MQTT_API_LOCK(c) ((void)(c))
#define MQTT_API_UNLOCK(c) ((void)(c))
#define ESP_LOGD(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
typedef struct {bool auto_reconnect;unsigned reconnect_timeout_ms;} config_t;
typedef struct {
    struct {struct {struct {uint64_t keepalive;} information;} connection;} mqtt_state;
    bool wait_for_ping_resp;
    uint64_t keepalive_tick,reconnect_tick;
    unsigned wait_timeout_ms,status_bits;
    int state,transport;
    struct {int event_id;} event;
    config_t *config;
} client_t,*esp_mqtt_client_handle_t;
static uint64_t now;
static unsigned pings,closes,disconnects;
static bool ping_fail;
static uint64_t platform_tick_get_ms(void) {return now;}
static void esp_transport_close(int transport) {(void)transport;++closes;}
static void esp_mqtt_dispatch_event_with_msgid(client_t *c) {
    assert(c->event.event_id==MQTT_EVENT_DISCONNECTED);++disconnects;
}
static int esp_mqtt_client_ping(client_t *c) {
    ++pings;if(ping_fail) return ESP_FAIL;
    c->keepalive_tick=now;return ESP_OK; // successful write updates keepalive_tick
}
static unsigned xEventGroupGetBits(unsigned bits) {return bits;}
static void xEventGroupClearBits(unsigned bits,unsigned clear) {(void)bits;(void)clear;}
static void xEventGroupWaitBits(unsigned bits,unsigned mask,bool a,bool b,unsigned ticks) {
    (void)bits;(void)mask;(void)a;(void)b;(void)ticks;
}
static unsigned max_poll_timeout(client_t *c,unsigned n) {(void)c;return n;}
''' + abort + keepalive + r'''
static void reconnect_tick(client_t *client) {
    for(unsigned once=0;once<1;once++) {
        switch(client->state) {
''' + reconnect + r'''
        default: assert(0);
        }
    }
}
int main(void) {
    config_t config={.auto_reconnect=true,.reconnect_timeout_ms=10000};
    client_t c={.config=&config,.state=MQTT_STATE_CONNECTED};
    c.mqtt_state.connection.information.keepalive=120;
    now=59999;assert(process_keepalive(&c)==ESP_OK && !pings);
    now=60000;assert(process_keepalive(&c)==ESP_OK && pings==1 && c.wait_for_ping_resp);
    now=179999;assert(process_keepalive(&c)==ESP_OK && !disconnects);
    now=180000;assert(process_keepalive(&c)==ESP_FAIL);
    assert(closes==1 && disconnects==1 && c.state==MQTT_STATE_WAIT_RECONNECT);
    now=190000;reconnect_tick(&c);assert(c.state==MQTT_STATE_WAIT_RECONNECT);
    now=190001;reconnect_tick(&c);assert(c.state==MQTT_STATE_INIT);
    // Same client can reconnect; no MCU restart is needed for this path.
    c.state=MQTT_STATE_CONNECTED;c.keepalive_tick=now;
    for(unsigned i=0;i<1440;i++) {
        now+=60000;assert(process_keepalive(&c)==ESP_OK && c.wait_for_ping_resp);
        c.wait_for_ping_resp=false; // production PINGRESP case; no PUBACK at all
        assert(c.state==MQTT_STATE_CONNECTED && disconnects==1);
    }
    // Transport failure still uses the existing disconnect/reconnect path.
    ping_fail=true;now+=60000;assert(process_keepalive(&c)==ESP_FAIL);
    assert(disconnects==2 && c.state==MQTT_STATE_WAIT_RECONNECT);
    now+=10001;reconnect_tick(&c);assert(c.state==MQTT_STATE_INIT);
}
'''
    path = tmp_path / "mqtt_recovery.c"
    path.write_text(program)
    binary = tmp_path / "mqtt_recovery"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(path), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    config = (PROJECT / "src/mqtt_registration.c").read_text()
    assert "disable_auto_reconnect" not in config and "disable_keepalive" not in config
    assert "esp_mqtt_client_reconnect(client)" in config
    # Library disposal of expired packets is not itself a connection reset.
    expiry = source[source.index("static void mqtt_delete_expired_messages("):
                    source.index("static inline int max_poll_timeout(")]
    assert expiry and "esp_mqtt_abort_connection" not in expiry
