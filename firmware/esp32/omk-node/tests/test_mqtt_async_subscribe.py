"""Test the exact queued SUBSCRIBE adapter, including contention and admission."""
from pathlib import Path
import runpy
import subprocess

PROJECT = Path(__file__).parents[1]


def test_subscribe_never_waits_for_mutex_or_transport(tmp_path):
    generated = tmp_path / "mqtt.c"
    runpy.run_path(str(PROJECT / "scripts/prepare_mqtt_diagnostics.py"))["prepare"](
        PROJECT / "managed_components/espressif__mqtt/mqtt_client.c", generated)
    source = generated.read_text()
    helper = source[source.index("int esp_mqtt_client_subscribe_bounded("):
                    source.index("/* The caller callback runs")]
    program = r'''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#define pdTRUE 1
#define MQTT_STATE_CONNECTED 1
#define MQTT_PROTOCOL_V_3_1_1 4
#define MQTT_MSG_TYPE_SUBSCRIBE 8
typedef struct {const char *filter;int qos;} esp_mqtt_topic_t;
typedef struct {struct {int protocol_ver;} information;struct {unsigned length;} outbound_message;} connection_t;
typedef struct {
    pthread_mutex_t *api_lock;
    int state;
    unsigned *outbox;
    struct {connection_t connection;int pending_msg_id,pending_msg_type,pending_publish_qos;} mqtt_state;
} *esp_mqtt_client_handle_t, client_t;
static pthread_mutex_t mutex;
static unsigned used, builds, enqueues;
static bool fail_build, fail_enqueue;
static int xSemaphoreTakeRecursive(pthread_mutex_t *m,unsigned wait) {
    assert(wait==0);return !pthread_mutex_trylock(m);
}
#define MQTT_API_UNLOCK(c) assert(!pthread_mutex_unlock((c)->api_lock))
static unsigned outbox_get_size(unsigned *n) {return *n;}
static void mqtt_reset_pending_message(esp_mqtt_client_handle_t c) {
    c->mqtt_state.pending_msg_id=c->mqtt_state.pending_msg_type=c->mqtt_state.pending_publish_qos=0;
}
static void mqtt_msg_subscribe(connection_t *c,const esp_mqtt_topic_t *t,int count,int *id) {
    assert(count==1 && t->qos==1);++builds;
    c->outbound_message.length=fail_build?0:strlen(t->filter)+7;*id=42;
}
static void *mqtt_enqueue(esp_mqtt_client_handle_t c,void *rest,int len) {
    assert(!rest && !len && c->mqtt_state.pending_msg_id==42);
    assert(c->mqtt_state.pending_msg_type==MQTT_MSG_TYPE_SUBSCRIBE);
    assert(!c->mqtt_state.pending_publish_qos);++enqueues;
    if(fail_enqueue) return NULL;
    *c->outbox+=c->mqtt_state.connection.outbound_message.length;
    return (void *)1; // QUEUED by the vendor outbox; no write/TRANSMITTED marking here.
}
''' + helper + r'''
static void *busy(void *c) {
    unsigned before=builds;
    assert(esp_mqtt_client_subscribe_bounded(c,"config",1,100)==-3);
    assert(builds==before);return NULL;
}
int main(void) {
    pthread_mutexattr_t attr;assert(!pthread_mutexattr_init(&attr));
    assert(!pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE));
    assert(!pthread_mutex_init(&mutex,&attr));pthread_mutexattr_destroy(&attr);
    client_t c={.api_lock=&mutex,.state=MQTT_STATE_CONNECTED,.outbox=&used};
    c.mqtt_state.connection.information.protocol_ver=MQTT_PROTOCOL_V_3_1_1;
    assert(!pthread_mutex_lock(&mutex));pthread_t t;
    assert(!pthread_create(&t,NULL,busy,&c));assert(!pthread_join(t,NULL));
    assert(!pthread_mutex_unlock(&mutex));
    used=85;assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==-2);
    assert(!builds && !enqueues);
    used=84;assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==42);
    assert(used<=100 && builds==1 && enqueues==1);
    used=0;fail_build=true;assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==-1);
    fail_build=false;fail_enqueue=true;
    assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==-1 && !used);
    fail_enqueue=false;c.state=0;
    assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==-1);
    c.state=1;c.mqtt_state.connection.information.protocol_ver=5;
    assert(esp_mqtt_client_subscribe_bounded(&c,"config",1,100)==-1);
    assert(esp_mqtt_client_subscribe_bounded(NULL,"config",1,100)==-1);
    assert(!pthread_mutex_destroy(&mutex));
}
'''
    path = tmp_path / "subscribe.c"
    path.write_text(program)
    binary = tmp_path / "subscribe"
    subprocess.run(["cc", "-D_GNU_SOURCE", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                    str(path), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
