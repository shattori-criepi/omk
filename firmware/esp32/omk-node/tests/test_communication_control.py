"""Production admission policy and the exact build-local MQTT adapter.

Host tests cover bounded allocation/admission and real mutex ownership, not
radio throughput. The vendor outbox owns every queued item until normal expiry.
"""
from pathlib import Path
import runpy
import subprocess

PROJECT = Path(__file__).parents[1]
SOURCE = PROJECT / "src"


def test_mesh_pressure_backoff_and_intermittent_recovery(tmp_path):
    program = tmp_path / "policy.c"
    program.write_text(r'''
#include <assert.h>
#include "mesh_tx_policy.h"
int main(void) {
    omk_mesh_tx_policy_t s={0};
    for(unsigned i=0;i<1000;i++) assert(mesh_tx_policy_allow(&s,0)); // healthy unchanged
    mesh_tx_policy_result(&s,100,true,false); // NO_MEMORY/TIMEOUT use same policy
    assert(s.interval_ms==1000 && !mesh_tx_policy_allow(&s,1099));
    assert(mesh_tx_policy_allow(&s,1100));
    assert(!mesh_tx_policy_allow(&s,1100)); // no burst after admission
    mesh_tx_policy_result(&s,1200,true,false);
    assert(s.interval_ms==2000 && !mesh_tx_policy_allow(&s,3199));
    mesh_tx_policy_result(&s,2000,false,true); // intermittent success must not reset
    assert(s.interval_ms==2000);
    mesh_tx_policy_result(&s,2100,true,false);
    assert(s.interval_ms==4000 && !s.successes);
    for(unsigned t=3000;t<60000;t+=1000) mesh_tx_policy_result(&s,t,true,false);
    assert(s.interval_ms==OMK_MESH_BACKOFF_MAX_MS);
    assert(!mesh_tx_policy_allow(&s,88999));
    assert(mesh_tx_policy_allow(&s,89000));
    assert(!mesh_tx_policy_allow(&s,89000));
    mesh_tx_policy_result(&s,89000,false,true);
    mesh_tx_policy_result(&s,89001,false,true);
    assert(s.interval_ms==OMK_MESH_BACKOFF_MAX_MS);
    mesh_tx_policy_result(&s,89002,false,true);
    assert(!s.interval_ms && mesh_tx_policy_allow(&s,89002));
    assert(mesh_tx_policy_allow(&s,89002));
    mesh_tx_policy_result(&s,UINT32_MAX-500,true,false);
    assert(!mesh_tx_policy_allow(&s,498));
    assert(mesh_tx_policy_allow(&s,499)); // wrap
    return 0;
}
''')
    binary = tmp_path / "policy"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                    str(program), str(SOURCE / "mesh_tx_policy.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_mqtt_atomic_install_capacity_and_busy_mutex(tmp_path):
    generated = tmp_path / "mqtt.c"
    runpy.run_path(str(PROJECT / "scripts/prepare_mqtt_diagnostics.py"))["prepare"](
        PROJECT / "managed_components/espressif__mqtt/mqtt_client.c", generated)
    source = generated.read_text()
    helper = source[source.index("int esp_mqtt_client_enqueue_bounded("):
                    source.index("esp_err_t esp_mqtt_client_register_event(")]
    program = tmp_path / "bounded.c"
    program.write_text(r'''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "mesh_liveness.h"
#include "communication_limits.h"
#define pdTRUE 1
typedef struct {pthread_mutex_t *api_lock; unsigned *outbox;} client_t;
typedef client_t *esp_mqtt_client_handle_t;
static pthread_mutex_t mqtt_lock;
static unsigned used, calls;
static bool fail_enqueue, ack_on_unlock, expire_on_unlock;
static omk_mqtt_progress_t progress;
static uint32_t now;
static int outbox_get_size(unsigned *box) {return *box;}
static int esp_mqtt_client_get_outbox_size(esp_mqtt_client_handle_t c) {return *c->outbox;}
static int xSemaphoreTakeRecursive(pthread_mutex_t *mutex,unsigned wait) {
    assert(wait==0);return pthread_mutex_trylock(mutex)==0;
}
static void xSemaphoreGiveRecursive(pthread_mutex_t *mutex) {
    assert(!pthread_mutex_unlock(mutex));
    if(ack_on_unlock || expire_on_unlock) {
        // Earliest possible terminal event, before the public helper returns.
        assert(progress.pending_id==42 && !progress.submitting);
        mqtt_progress_event(&progress,now,42,ack_on_unlock);
        ack_on_unlock=expire_on_unlock=false;
    }
}
static int esp_mqtt_client_enqueue(esp_mqtt_client_handle_t c,const char *topic,
    const char *data,int len,int qos,int retain,bool store) {
    (void)data;(void)retain;assert(store);
    assert(!pthread_mutex_lock(c->api_lock)); // actual API takes recursive lock
    assert(!pthread_mutex_unlock(c->api_lock));
    ++calls;
    if(fail_enqueue) return -1;
    *c->outbox+=(unsigned)len+strlen(topic)+9;
    return qos?42:0;
}
''' + helper + r'''
static void submitted(int id) {mqtt_progress_result(&progress,now,id);}
static void *busy_caller(void *ptr) {
    client_t *c=ptr;
    unsigned before=calls;
    assert(esp_mqtt_client_enqueue_bounded(c,"ble","payload",0,0,0,OMK_MQTT_DATA_BYTES,NULL)==-3);
    assert(esp_mqtt_client_get_outbox_size_nowait(c)==-3);
    assert(calls==before);
    return NULL;
}
int main(void) {
    pthread_mutexattr_t attr;assert(!pthread_mutexattr_init(&attr));
    assert(!pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE));
    assert(!pthread_mutex_init(&mqtt_lock,&attr));pthread_mutexattr_destroy(&attr);
    client_t c={.api_lock=&mqtt_lock,.outbox=&used};
    assert(!pthread_mutex_lock(&mqtt_lock));
    pthread_t thread;assert(!pthread_create(&thread,NULL,busy_caller,&c));
    assert(!pthread_join(thread,NULL));assert(!pthread_mutex_unlock(&mqtt_lock));
    assert(mqtt_progress_begin(&progress,now,true));
    for(int i=1;i<=100;i++) mqtt_progress_event(&progress,now,i,true); // old overflow
    ack_on_unlock=true;
    assert(esp_mqtt_client_enqueue_bounded(&c,"probe","1",1,1,0,OMK_MQTT_OUTBOX_BYTES,submitted)==42);
    assert(!progress.pending_id && progress.ack_count==1);
    used=OMK_MQTT_DATA_BYTES-1;
    unsigned before=calls;
    assert(esp_mqtt_client_enqueue_bounded(&c,"data","x",1,0,0,OMK_MQTT_DATA_BYTES,NULL)==-2);
    assert(calls==before && used==OMK_MQTT_DATA_BYTES-1); // includes packet overhead
    now=30000;assert(mqtt_progress_begin(&progress,now,true));
    assert(esp_mqtt_client_enqueue_bounded(&c,"probe","1",1,1,0,OMK_MQTT_OUTBOX_BYTES,submitted)==42);
    assert(progress.pending_id==42 && progress.ack_count==1); // missing PUBACK
    for(now=60000;now<=7200000;now+=30000) assert(!mqtt_progress_begin(&progress,now,true));
    assert(!mqtt_progress_is_fresh(&progress,now));
    // Vendor expiration, not a forced deletion, releases exactly this ID.
    mqtt_progress_event(&progress,now,42,false);
    assert(mqtt_progress_begin(&progress,now,true));
    expire_on_unlock=true;
    assert(esp_mqtt_client_enqueue_bounded(&c,"probe","1",1,1,0,OMK_MQTT_OUTBOX_BYTES,submitted)==42);
    assert(!progress.pending_id && progress.ack_count==1);
    now+=30000;assert(mqtt_progress_begin(&progress,now,true));
    fail_enqueue=true;
    assert(esp_mqtt_client_enqueue_bounded(&c,"probe","1",1,1,0,OMK_MQTT_OUTBOX_BYTES,submitted)==-1);
    assert(!progress.submitting && !progress.pending_id);
    assert(!mqtt_progress_begin(&progress,now+29999,true));
    fail_enqueue=false;used=OMK_MQTT_OUTBOX_BYTES;
    assert(esp_mqtt_client_enqueue_diagnostic(&c,"diag","payload")==-2);
    assert(esp_mqtt_client_enqueue_bounded(&c,"probe","1",1,1,0,OMK_MQTT_OUTBOX_BYTES,NULL)==-2);
    used=0;assert(esp_mqtt_client_enqueue_diagnostic(&c,"diag","payload")==0);
    assert(esp_mqtt_client_get_outbox_size_nowait(&c)==20);
    return 0;
}
''')
    binary = tmp_path / "bounded"
    subprocess.run(["cc", "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-pthread",
                    "-I", str(SOURCE), str(program), str(SOURCE / "mesh_liveness.c"),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_ip_timeout_survives_reselection_but_not_root_promotion(tmp_path):
    program = tmp_path / "ip.c"
    program.write_text(r''' 
#include <assert.h>
#include "mesh_recovery.h"
int main(void) {
    omk_mesh_recovery_t s;
    omk_mesh_root_recovery_t r;
    mesh_recovery_init(&s,1);s.jitter_ms=0;
    mesh_root_recovery_init(&r);
    omk_mesh_recovery_input_t in={.started=true};
    mesh_recovery_choose(&s,0,&in,&r);
    assert(!s.ip_wait_active); // Initial parent discovery is the existing policy.
    in.parent_connected=true;
    assert(mesh_recovery_choose(&s,1000000,&in,&r)==OMK_RECOVERY_NONE);
    assert(s.ip_wait_active && s.ip_since_ms==1000000);
    assert(mesh_recovery_choose(&s,1179999,&in,&r)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&s,1180000,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    mesh_recovery_complete(&s,1180000,OMK_RECOVERY_CHILD_RESELECT,true);
    in.parent_connected=false;
    mesh_recovery_choose(&s,1180001,&in,&r);
    in.parent_connected=true;
    assert(mesh_recovery_choose(&s,1181000,&in,&r)==OMK_RECOVERY_NONE);
    assert(s.ip_since_ms==1000000); // Own disconnect/rejoin cannot extend deadline.
    assert(mesh_recovery_choose(&s,1599999,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    assert(mesh_recovery_choose(&s,1600000,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    omk_mesh_recovery_token_t selected=mesh_recovery_action_token(&s,OMK_RECOVERY_CHILD_RESELECT);
    in.is_root=true;
    assert(mesh_recovery_choose(&s,1600001,&in,&r)==OMK_RECOVERY_NONE);
    assert(!s.ip_wait_active && !mesh_recovery_action_matches(&s,&selected));
    for(unsigned t=1600002;t<8000000;t+=1000)
        assert(mesh_recovery_choose(&s,t,&in,&r)<=OMK_RECOVERY_ROOT_VOTE);
    in.is_root=false;
    assert(mesh_recovery_choose(&s,8000000,&in,&r)==OMK_RECOVERY_NONE);
    assert(s.ip_since_ms==8000000); // Newly demoted child gets DHCP grace.
    in.has_ip=true;
    mesh_recovery_choose(&s,8000001,&in,&r);
    assert(!s.ip_wait_active);
    return 0;
}
''')
    binary = tmp_path / "ip"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                    str(program), *(str(SOURCE / name) for name in
                    ("mesh_recovery.c", "mesh_root_recovery.c", "mesh_liveness.c")),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_ip_deadline_with_subsecond_parent_flaps(tmp_path):
    program = tmp_path / "ip_flap.c"
    program.write_text(r'''
#include <assert.h>
#include "mesh_recovery.h"
int main(void) {
    omk_mesh_recovery_t s;
    omk_mesh_root_recovery_t r;
    mesh_recovery_init(&s,1);s.jitter_ms=0;
    mesh_root_recovery_init(&r);
    omk_mesh_recovery_input_t in={.started=true,.parent_connected=true};
    mesh_recovery_observe(&s,0,&in);
    in.parent_connected=false;mesh_recovery_observe(&s,500,&in);
    unsigned reselects=0;
    for(unsigned t=1000;t<=600000;t+=1000) {
        // The event loop sees the 500 ms association; every monitor tick sees down.
        if(t%60000==0) {
            in.parent_connected=true;mesh_recovery_observe(&s,t-750,&in);
            in.parent_connected=false;mesh_recovery_observe(&s,t-250,&in);
        }
        omk_mesh_recovery_action_t a=mesh_recovery_choose(&s,t,&in,&r);
        if(a==OMK_RECOVERY_CHILD_RESELECT) {
            assert(t==180000 || t==480000);++reselects;
            mesh_recovery_complete(&s,t,a,true);
        }
        assert(a==OMK_RECOVERY_NONE || a==OMK_RECOVERY_CHILD_RESELECT);
        assert(s.reason==OMK_RECOVERY_IP_TIMEOUT);
    }
    assert(reselects==2 && s.ip_since_ms==0);
    omk_mesh_recovery_token_t selected=mesh_recovery_action_token(&s,OMK_RECOVERY_CHILD_RESELECT);
    // Parent flaps preserve the IP episode; reselection continues without reboot.
    in.parent_connected=true;mesh_recovery_observe(&s,600100,&in);
    in.parent_connected=false;mesh_recovery_observe(&s,600600,&in);
    assert(mesh_recovery_action_matches(&s,&selected));
    s.jitter_ms=120000;
    assert(mesh_recovery_choose(&s,779999,&in,&r)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&s,780000,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    s.jitter_ms=0;
    mesh_root_recovery_mark_executed(&r,780000);
    assert(mesh_recovery_choose(&s,899999,&in,&r)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&s,900000,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    mesh_recovery_complete(&s,900000,OMK_RECOVERY_CHILD_RESELECT,true);
    assert(mesh_recovery_choose(&s,900001,&in,&r)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&s,3601000,&in,&r)==OMK_RECOVERY_CHILD_RESELECT);
    in.parent_connected=in.has_ip=in.mqtt_started=in.mqtt_connected=in.mqtt_progress=true;
    assert(mesh_recovery_choose(&s,3601002,&in,&r)==OMK_RECOVERY_NONE);
    assert(!s.ip_wait_active && s.stage==OMK_RECOVERY_HEALTHY);
    assert(!mesh_recovery_action_matches(&s,&selected));
    in.is_root=true;in.has_ip=false;in.parent_connected=false;
    assert(mesh_recovery_choose(&s,3601004,&in,&r)<=OMK_RECOVERY_ROOT_VOTE);
    assert(!s.ip_wait_active && !s.explicit_accepted);
    return 0;
}
''')
    binary=tmp_path / "ip_flap"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                    str(program), *(str(SOURCE / n) for n in
                    ("mesh_recovery.c", "mesh_root_recovery.c", "mesh_liveness.c")),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_registration_publish_packet_capacity_and_mutex(tmp_path):
    generated = tmp_path / "mqtt.c"
    runpy.run_path(str(PROJECT / "scripts/prepare_mqtt_diagnostics.py"))["prepare"](
        PROJECT / "managed_components/espressif__mqtt/mqtt_client.c", generated)
    source = generated.read_text()
    helper = source[source.index("int esp_mqtt_client_publish_bounded("):
                    source.index("/* OMK uses MQTT 3.1.1.")] + source[source.index("int esp_mqtt_client_enqueue_bounded("):source.index("esp_err_t esp_mqtt_client_register_event(")]
    program = tmp_path / "registration_bound.c"
    program.write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>
#include "communication_limits.h"
typedef struct {pthread_mutex_t *api_lock; unsigned *outbox;} client_t;
typedef client_t *esp_mqtt_client_handle_t;
static pthread_mutex_t mutex;
static unsigned used, calls;
static int publish_result=42;
#define MQTT_API_LOCK(c) assert(!pthread_mutex_lock((c)->api_lock))
#define MQTT_API_UNLOCK(c) assert(!pthread_mutex_unlock((c)->api_lock))
static unsigned outbox_get_size(unsigned *box) { return *box; }
static int esp_mqtt_client_get_outbox_size(esp_mqtt_client_handle_t c) {return *c->outbox;}
#define pdTRUE 1
static int xSemaphoreTakeRecursive(pthread_mutex_t *m, unsigned wait) {
    assert(wait==0);return !pthread_mutex_trylock(m);
}
static void xSemaphoreGiveRecursive(pthread_mutex_t *m) {assert(!pthread_mutex_unlock(m));}
int esp_mqtt_client_enqueue_bounded(esp_mqtt_client_handle_t,const char *,const char *,int,int,int,unsigned,void (*)(int));
static void *check_owned(void *ptr) {
    assert(pthread_mutex_trylock(ptr)==EBUSY);return NULL;
}
static int esp_mqtt_client_enqueue(esp_mqtt_client_handle_t c,const char *topic,
    const char *data,int len,int qos,int retain,bool store) {
    assert(store);
    (void)data;assert(qos==1 && retain==1);++calls;
    // Another task cannot enqueue between the size check and outbox insertion.
    pthread_t t;assert(!pthread_create(&t,NULL,check_owned,c->api_lock));
    assert(!pthread_join(t,NULL));
    MQTT_API_LOCK(c); // vendor enqueue recursively acquires the same mutex
    if(publish_result>=0) *c->outbox+=len+strlen(topic)+9;
    MQTT_API_UNLOCK(c);
    return publish_result;
}
''' + helper + r'''
int main(void) {
    pthread_mutexattr_t attr;assert(!pthread_mutexattr_init(&attr));
    assert(!pthread_mutexattr_settype(&attr,PTHREAD_MUTEX_RECURSIVE));
    assert(!pthread_mutex_init(&mutex,&attr));pthread_mutexattr_destroy(&attr);
    client_t c={.api_lock=&mutex,.outbox=&used};
    const char *topics[]={"a","omk/node/020000000001/registration/status"};
    const char *payloads[]={"","registered"};
    for(unsigned t=0;t<2;t++) for(unsigned p=0;p<2;p++) for(int delta=-1;delta<=1;delta++) {
        unsigned required=strlen(topics[t])+strlen(payloads[p])+9;
        used=OMK_MQTT_OUTBOX_BYTES-required+delta;
        unsigned before=calls, size_before=used;
        int id=esp_mqtt_client_publish_bounded(&c,topics[t],payloads[p],0,1,1,OMK_MQTT_OUTBOX_BYTES);
        if(delta>0) assert(id==-2 && calls==before && used==size_before);
        else assert(id==42 && calls==before+1 && used<=OMK_MQTT_OUTBOX_BYTES);
    }
    used=OMK_MQTT_OUTBOX_BYTES+1;
    assert(esp_mqtt_client_publish_bounded(&c,"a","",0,1,1,OMK_MQTT_OUTBOX_BYTES)==-2);
    used=0;publish_result=-1;
    assert(esp_mqtt_client_publish_bounded(&c,"a","hello",3,1,1,OMK_MQTT_OUTBOX_BYTES)==-1);
    assert(!used);publish_result=42;
    assert(esp_mqtt_client_publish_bounded(&c,"a","hello",3,1,1,OMK_MQTT_OUTBOX_BYTES)==42);
    assert(used==13); // explicit payload length honored
    assert(esp_mqtt_client_publish_bounded(NULL,"a","",0,1,1,OMK_MQTT_OUTBOX_BYTES)==-1);
    assert(!pthread_mutex_destroy(&mutex)); // every path releases ownership
    return 0;
}
''')
    binary = tmp_path / "registration_bound"
    subprocess.run(["cc", "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-pthread",
                    "-I", str(SOURCE), str(program), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
