"""Real NVS adapter + production dispatch with durable-store failure injection."""
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def storage_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh-store")
    (directory / "esp_err.h").write_text("""#pragma once
    typedef int esp_err_t;
    #define ESP_OK 0
    #define ESP_FAIL (-1)
    #define ESP_ERR_INVALID_STATE (-2)
    #define ESP_ERR_NVS_NOT_FOUND (-3)
    """)
    (directory / "nvs.h").write_text("""#pragma once
    #include <stddef.h>
    #include "esp_err.h"
    typedef int nvs_handle_t;
    #define NVS_READONLY 0
    #define NVS_READWRITE 1
    esp_err_t nvs_open(const char *, int, nvs_handle_t *);
    esp_err_t nvs_get_blob(nvs_handle_t, const char *, void *, size_t *);
    esp_err_t nvs_set_blob(nvs_handle_t, const char *, const void *, size_t);
    esp_err_t nvs_commit(nvs_handle_t);
    void nvs_close(nvs_handle_t);
    """)
    network = (SOURCE / "mesh_network.c").read_text()
    dispatch = network[network.index("static bool restart_still_valid("):network.index("static void try_recovery(")]
    monitor = network[network.index("static void try_recovery("):network.index("static void recovery_task(")]
    formatter = network[network.index("static void publish_recovery_status("):network.index("/* The first observed role")]
    program = directory / "store.c"
    program.write_text(r'''
#include <assert.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvs.h"
#include "mesh_recovery_store.h"
#include "mesh_liveness.h"
static unsigned recovery_lock_depth;
#define portENTER_CRITICAL(lock) assert(++recovery_lock_depth==1)
#define portEXIT_CRITICAL(lock) assert(--recovery_lock_depth==0)
static bool progress_seen;
static omk_mqtt_progress_t mqtt_registration_get_progress(void) { return (omk_mqtt_progress_t){.ack_seen=progress_seen}; }
static bool mqtt_registration_is_connected(void) { return true; }
static bool mqtt_registration_is_started(void) { return true; }
static uint32_t mqtt_registration_get_disconnect_count(void) { return 0; }
#define ND_RECOVERY 0
#define OMK_MESH_CONTROL_EVENT "control"
static unsigned deferred_posts;
static omk_mesh_recovery_token_t posted_selection;
static omk_mesh_recovery_input_t recovery_snapshot;
static bool recovery_action_pending, recovery_action_attempted, rearm_attempted;
static uint32_t recovery_action_ms, rearm_attempt_ms;
static bool recovery_control_waiting, restart_selected_root;
static uint32_t recovery_control_since_ms;
static omk_mesh_recovery_t restart_selection;
static esp_err_t post_result;
#define OMK_MESH_STATUS_INTERVAL_MS 30000
static uint32_t clock_ms=UINT32_MAX;
static uint32_t network_diag_begin(int op) { (void)op; return 1; }
static void network_diag_end(uint32_t token,int result,bool failure) { (void)token;(void)result;(void)failure; }
static void network_diag_recovery(uint32_t flags,uint32_t stage,uint32_t action) {
    (void)flags;(void)stage;(void)action;
}
static esp_err_t esp_event_post(const char *base,int id,const void *data,size_t size,unsigned wait) {
    assert(!strcmp(base,"control") && id==1 && data && size==sizeof(omk_mesh_recovery_token_t) && wait==0);
    posted_selection=*(const omk_mesh_recovery_token_t *)data;
    ++deferred_posts; return post_result; /* Deliberately never execute: event loop blocked. */
}
static omk_mesh_recovery_record_t durable, pending;
static bool exists, root_on_commit, root_on_reason, ack_on_commit;
static int failure, opens, gets, commits, closes;
static omk_mesh_recovery_t recovery;
static omk_mesh_recovery_record_t recovery_record;
static omk_mesh_root_recovery_t root_recovery;
static bool root, parent_connected, is_rootless, role_change;
static unsigned selections, enables, connects, votes, restarts, reasons;
static esp_err_t api_result;
static void (*api_interleave)(void);
#define TAG "test"
#define ESP_LOGI(tag, ...) do { (void)(tag); if (0) printf(__VA_ARGS__); } while (0)
#define ESP_LOGE ESP_LOGI
static const char *esp_err_to_name(esp_err_t err) { return err == ESP_OK ? "ESP_OK" : "error"; }
esp_err_t nvs_open(const char *ns,int mode,nvs_handle_t *h) {
    assert(!strcmp(ns,"omk_recovery")); ++opens;
    if (failure==1 || (failure==4 && mode==NVS_READONLY)) return ESP_FAIL;
    *h=mode; return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t h,const char *key,void *out,size_t *size) {
    assert(!strcmp(key,"budget_v1")); ++gets;
    if (failure==5) return ESP_FAIL;
    if (!exists) return ESP_ERR_NVS_NOT_FOUND;
    assert(*size==sizeof(durable)); memcpy(out,&durable,sizeof(durable));
    if (failure==6 && h==NVS_READONLY) ((omk_mesh_recovery_record_t *)out)->spent ^= 1;
    if (failure==7) --*size;
    return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t h,const char *key,const void *in,size_t size) {
    assert(h==NVS_READWRITE && !strcmp(key,"budget_v1") && size==sizeof(pending));
    if (failure==2) return ESP_FAIL;
    memcpy(&pending,in,size); return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t h) {
    assert(h==NVS_READWRITE); ++commits;
    if (failure==3) return ESP_FAIL;
    durable=pending; exists=true; if (root_on_commit) root=true; if(ack_on_commit) progress_seen=true; return ESP_OK;
}
void nvs_close(nvs_handle_t h) { (void)h; ++closes; }
static bool esp_mesh_is_root(void) { return root; }
static esp_err_t esp_mesh_set_self_organized(bool enable,bool select) {
    assert(enable && !recovery_lock_depth); ++enables;
    if (select) ++selections;
    if (role_change) root=!root;
    if(api_interleave && select) api_interleave();
    return api_result;
}
static esp_err_t esp_mesh_connect(void) {
    assert(root && !parent_connected && !recovery_lock_depth); ++connects; if(api_interleave) api_interleave(); return api_result;
}
static void try_root_recovery(void) { ++votes; }
static void boot_diagnostics_record_restart_reason(const char *reason) {
    assert(!strcmp(reason,"mesh_parent_loss_timeout") || !strcmp(reason,"mesh_mqtt_liveness_timeout") || !strcmp(reason,"mesh_ip_timeout"));
    ++reasons;
    if (root_on_reason) root=true;
}
static void esp_restart(void) {
    assert(exists && durable.spent==1 && gets>0 && commits>0 && closes>=2);
    assert(reasons==restarts+1); ++restarts;
}
static uint32_t uptime_seconds(void) { return UINT32_MAX; }
static uint32_t uptime_milliseconds(void) { return clock_ms; }
static esp_err_t mqtt_registration_publish_mesh_recovery_status(const char *payload) {
    assert(strlen(payload)<768 && strlen(payload)<1152); puts(payload); return ESP_OK;
}
''' + dispatch + monitor + formatter + r'''
static void dispatch_now(omk_mesh_recovery_action_t action, uint32_t now) {
    omk_mesh_recovery_token_t token;
    portENTER_CRITICAL(&recovery_lock);
    recovery_snapshot.is_root=root;
    observe_control_role_locked();
    token=mesh_recovery_action_token(&recovery,action);
    portEXIT_CRITICAL(&recovery_lock);
    run_recovery_action(action,now,&token);
}
static void select_restart(omk_mesh_recovery_reason_t reason) {
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true,.mqtt_started=true,.mqtt_connected=true};
    if (reason==OMK_RECOVERY_MQTT_ONLY) recovery_snapshot.parent_connected=recovery_snapshot.has_ip=true;
    parent_connected=recovery_snapshot.parent_connected;
    mesh_recovery_init(&recovery,false,1); recovery.jitter_ms=0;
    mesh_recovery_choose(&recovery,0,&recovery_snapshot,&root_recovery);
    if(reason==OMK_RECOVERY_NO_PARENT) mesh_recovery_note_no_parent(&recovery,1);
    if(reason!=OMK_RECOVERY_MQTT_ONLY) mesh_recovery_complete(&recovery,120000,OMK_RECOVERY_CHILD_RESELECT,true);
    clock_ms=600000;
    assert(mesh_recovery_choose(&recovery,clock_ms,&recovery_snapshot,&root_recovery)==OMK_RECOVERY_RESTART);
    restart_selection=recovery; restart_selected_root=false;
}
static void load_failures(void) {
    for (int f=1;f<=7;f++) {
        failure=f; exists=true; durable=(omk_mesh_recovery_record_t){1,1,9,OMK_RECOVERY_NO_PARENT};
        esp_err_t err=mesh_recovery_store_load(&recovery_record);
        if (f==1 || f==5 || f==7) assert(err!=ESP_OK && recovery_record.spent==1);
        else assert(err==ESP_OK && recovery_record.spent==1);
    }
    failure=0;
    for(int f=0;f<3;f++) {
        durable=(omk_mesh_recovery_record_t){1,0,9,OMK_RECOVERY_NO_PARENT};
        if(f==0) durable.version=99;
        if(f==1) durable.spent=2;
        if(f==2) durable.reason=UINT32_MAX;
        assert(mesh_recovery_store_load(&recovery_record)!=ESP_OK && recovery_record.spent==1);
    }
}
static void legacy_reasons(void) {
    /* Literal wire values catch accidental renumbering when reasons disappear. */
    assert(OMK_RECOVERY_MQTT_ONLY == 6 && OMK_RECOVERY_ROOT_LINK == 9);
    for (uint32_t reason=0; reason<=9; ++reason) {
        exists=true;
        durable=(omk_mesh_recovery_record_t){1,1,12,reason};
        assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
        assert(recovery_record.spent==1 && recovery_record.restart_count==12);
        assert(recovery_record.reason==reason);
        mesh_recovery_init(&recovery,recovery_record.spent,1);
        assert(recovery.restart_spent);
        const char *name=mesh_recovery_reason_name(recovery_record.reason);
        if (reason==7 || reason==8) assert(!strcmp(name,"none"));
        if (reason==9) assert(!strcmp(name,"root_link_unhealthy"));
    }
}
static void restart_failure(int f) {
    failure=0; assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    assert(recovery_record.spent==0);
    select_restart(OMK_RECOVERY_PARENT_LOSS);
    failure=f;
    dispatch_now(OMK_RECOVERY_RESTART,600000);
    assert(restarts==0 && reasons==0 && recovery.restart_spent);
    failure=0;
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    if(f>=4) assert(recovery_record.spent==1); /* Commit succeeded before read-back failure. */
}
static void boots(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK && !recovery_record.spent);
    select_restart(OMK_RECOVERY_NO_PARENT);
    dispatch_now(OMK_RECOVERY_RESTART,600000);
    assert(restarts==1);
    for (int boot=0;boot<4;boot++) {
        assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
        assert(recovery_record.spent==1 && recovery_record.restart_count==1);
        mesh_recovery_init(&recovery,recovery_record.spent,1);
        omk_mesh_recovery_input_t in={.started=true};
        for(uint32_t t=0;t<OMK_MESH_RESTART_RETRY_MS;t+=30000) {
            omk_mesh_recovery_action_t a=mesh_recovery_choose(&recovery,t,&in,&root_recovery);
            assert(a!=OMK_RECOVERY_RESTART && a!=OMK_RECOVERY_REARM);
            dispatch_now(a,t);
        }
        assert(restarts==1);
    }
    omk_mesh_recovery_input_t in={.started=true,.parent_connected=true,.has_ip=true,.mqtt_connected=true,.mqtt_progress=true};
    assert(mesh_recovery_choose(&recovery,4000000,&in,&root_recovery)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&recovery,4899999,&in,&root_recovery)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&recovery,4900000,&in,&root_recovery)==OMK_RECOVERY_REARM);
    failure=3;
    dispatch_now(OMK_RECOVERY_REARM,4900000);
    assert(recovery.restart_spent && durable.spent);
    failure=0;
    dispatch_now(OMK_RECOVERY_REARM,4900000);
    assert(!recovery.restart_spent);
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK && !recovery_record.spent);
    assert(recovery_record.restart_count==1 && recovery_record.reason==OMK_RECOVERY_NO_PARENT);
}
static void apis(void) {
    recovery_snapshot.started=true;
    dispatch_now(OMK_RECOVERY_CHILD_RESELECT,0);
    assert(selections==1 && enables==1 && !connects && recovery.explicit_accepted);
    root=true;
    dispatch_now(OMK_RECOVERY_CHILD_RESELECT,30000); /* Became root since decision. */
    assert(enables==1);
    dispatch_now(OMK_RECOVERY_ROUTER_RECONNECT,60000);
    assert(enables==2 && selections==1 && connects==1);
    root=false;
    dispatch_now(OMK_RECOVERY_ROUTER_RECONNECT,90000);
    assert(enables==2);
    root=true; api_result=ESP_FAIL;
    dispatch_now(OMK_RECOVERY_ROUTER_RECONNECT,120000);
    assert(enables==3 && connects==1); /* Failed enable cannot call connect. */
    api_result=ESP_OK; role_change=true;
    dispatch_now(OMK_RECOVERY_ROUTER_RECONNECT,150000);
    assert(enables==4 && connects==1); /* Role changed inside enable. */
    assert(!votes && !restarts);
}
static void payload(void) {
    recovery.stage=OMK_RECOVERY_RESTART_PENDING;
    recovery.reason=OMK_RECOVERY_ROOT_LINK;
    recovery.no_parent_found_count=recovery.stop_reconnection_count=UINT32_MAX;
    recovery.parent_reselection_count=UINT32_MAX;
    recovery.last_scan_times=INT_MIN;
    recovery.loss_active=true;
    recovery_record.restart_count=UINT32_MAX;
    recovery_record.reason=OMK_RECOVERY_ROOT_LINK;
    publish_recovery_status(0xffffffffffffULL);
}

static void blocked_event_loop(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    mesh_recovery_init(&recovery,false,1); recovery.jitter_ms=0;
    parent_connected=true;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true,.parent_connected=true,.has_ip=true};
    clock_ms=0; try_recovery();
    assert(recovery.stage==OMK_RECOVERY_MQTT_WAIT);
    clock_ms=179999; try_recovery(); assert(!restarts);
    clock_ms=180000; try_recovery(); assert(restarts==1);
    assert(!deferred_posts); /* Reboot does not depend on the stalled event loop. */
    clock_ms=180000+OMK_MESH_RESTART_RETRY_MS-1; try_recovery(); assert(restarts==1);
    clock_ms=180000+OMK_MESH_RESTART_RETRY_MS; try_recovery(); assert(restarts==2 && durable.spent);
    root=true;
    clock_ms=10000000; try_recovery(); assert(restarts==2);
}

static void blocked_mesh_action(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    mesh_recovery_init(&recovery,false,1); recovery.jitter_ms=0;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true};
    clock_ms=0; try_recovery();
    clock_ms=120000; try_recovery();
    assert(deferred_posts==1 && recovery_action_pending);
    for(clock_ms=121000;clock_ms<600000;clock_ms+=1000) try_recovery();
    assert(deferred_posts==1 && !restarts); /* No storm while Mesh API is stuck. */
    try_recovery(); assert(restarts==1); /* Does not require API return. */
}

static void root_during_save(bool after_reason) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    select_restart(OMK_RECOVERY_MQTT_ONLY);
    root_on_commit=!after_reason; root_on_reason=after_reason;
    dispatch_now(OMK_RECOVERY_RESTART,180000);
    assert(root && !restarts && commits==1 && durable.spent && recovery.restart_spent);
    assert(reasons==(unsigned)after_reason);
}
static void root_restart_guard(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    recovery.reason=OMK_RECOVERY_MQTT_ONLY; root=true;
    dispatch_now(OMK_RECOVERY_RESTART,180000);
    assert(!restarts && !commits);
    recovery.reason=OMK_RECOVERY_PARENT_LOSS; parent_connected=true;
    dispatch_now(OMK_RECOVERY_RESTART,600000);
    assert(!restarts && !commits);
}
static void full_queue(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    mesh_recovery_init(&recovery,false,1); recovery.jitter_ms=0;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true};
    post_result=ESP_FAIL;
    for(clock_ms=0;clock_ms<600000;clock_ms+=1000) try_recovery();
    assert(!restarts && deferred_posts<=16 && !recovery_action_pending);
    try_recovery(); assert(restarts==1);
}
static void reason_race(bool demotion) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    select_restart(OMK_RECOVERY_MQTT_ONLY);
    root=!demotion; restart_selected_root=demotion;
    recovery_snapshot.is_root=root;
    recovery_snapshot.parent_connected=parent_connected=false;
    recovery_snapshot.has_ip=false;
    mesh_recovery_observe(&recovery,++clock_ms,&recovery_snapshot);
    dispatch_now(OMK_RECOVERY_RESTART,clock_ms);
    assert(!restarts && !commits);
}
static void wait_ip(bool spent) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    mesh_recovery_init(&recovery,spent,1); recovery.jitter_ms=0;
    parent_connected=true;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true,.parent_connected=true};
    uint32_t deadline=spent?OMK_MESH_RESTART_RETRY_MS:OMK_MESH_IP_RESTART_MS;
    for(clock_ms=0;clock_ms<deadline;clock_ms+=1000) try_recovery();
    assert(!restarts && deferred_posts==1);
    try_recovery(); assert(restarts==1);
}
static void recovered_during_save(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    select_restart(OMK_RECOVERY_MQTT_ONLY);
    ack_on_commit=true;
    dispatch_now(OMK_RECOVERY_RESTART,clock_ms);
    assert(progress_seen && !restarts && commits==1 && durable.spent);
}
static void root_isolation(bool control_available) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    mesh_recovery_init(&recovery,false,1); recovery.jitter_ms=0;
    root=true;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true,.is_root=true};
    clock_ms=0;try_recovery();
    clock_ms=120000;try_recovery();
    if(control_available) {
        dispatch_now(OMK_RECOVERY_ROUTER_RECONNECT,clock_ms);
        recovery_action_pending=recovery_control_waiting=false;
        clock_ms=600000;try_recovery();assert(restarts==1);
    } else {
        for(clock_ms=121000;clock_ms<=7200000;clock_ms+=1000) try_recovery();
        assert(!restarts); // Child stalled-control fallback must never reboot Root.
    }
}
static void switch_and_observe(void) {
    root=!root;
    recovery_snapshot.is_root=root;
    portENTER_CRITICAL(&recovery_lock);
    mesh_recovery_observe(&recovery,++clock_ms,&recovery_snapshot);
    portEXIT_CRITICAL(&recovery_lock);
    assert(!recovery.explicit_accepted);
}
static void late_completion(bool was_root) {
    root=was_root;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true,.is_root=root};
    mesh_recovery_init(&recovery,false,1);recovery.jitter_ms=0;
    mesh_recovery_choose(&recovery,0,&recovery_snapshot,&root_recovery);
    clock_ms=120000;
    omk_mesh_recovery_action_t action=mesh_recovery_choose(&recovery,clock_ms,&recovery_snapshot,&root_recovery);
    api_interleave=switch_and_observe;
    dispatch_now(action,clock_ms);
    assert(!recovery.explicit_accepted && !recovery.attempted);
    assert(mesh_recovery_choose(&recovery,clock_ms+600000,&recovery_snapshot,&root_recovery)!=OMK_RECOVERY_RESTART);
}

static void role_round_trip(void) {
    switch_and_observe();switch_and_observe();
}
static void new_loss_episode(void) {
    portENTER_CRITICAL(&recovery_lock);
    recovery_snapshot.parent_connected=recovery_snapshot.has_ip=true;
    mesh_recovery_observe(&recovery,++clock_ms,&recovery_snapshot);
    recovery_snapshot.parent_connected=recovery_snapshot.has_ip=false;
    mesh_recovery_observe(&recovery,++clock_ms,&recovery_snapshot);
    portEXIT_CRITICAL(&recovery_lock);
}
static void stale_generation(bool role_trip, bool queued) {
    mesh_recovery_init(&recovery,false,1);recovery.jitter_ms=0;
    recovery_snapshot=(omk_mesh_recovery_input_t){.started=true};
    clock_ms=0;try_recovery();
    clock_ms=120000;try_recovery();
    assert(deferred_posts==1 && posted_selection.action==OMK_RECOVERY_CHILD_RESELECT);
    api_interleave=role_trip?role_round_trip:new_loss_episode;
    if(queued) { api_interleave();api_interleave=NULL; }
    run_recovery_action(posted_selection.action,clock_ms,&posted_selection);
    assert(!recovery.explicit_accepted && !recovery.attempted);
    assert(selections==(queued?0U:1U));
    assert(!root && recovery.generation!=posted_selection.generation);
    // A fresh selection in the new episode still executes and can complete.
    api_interleave=NULL;clock_ms+=120000;
    assert(mesh_recovery_choose(&recovery,clock_ms,&recovery_snapshot,&root_recovery)==OMK_RECOVERY_CHILD_RESELECT);
    dispatch_now(OMK_RECOVERY_CHILD_RESELECT,clock_ms);
    assert(recovery.explicit_accepted && recovery.attempted);
}
int main(int argc,char **argv) {
    assert(argc==2); (void)esp_err_to_name(ESP_OK);
    if(!strcmp(argv[1],"late_promotion")) late_completion(false);
    else if(!strcmp(argv[1],"late_demotion")) late_completion(true);
    else if(!strcmp(argv[1],"late_role_round_trip")) stale_generation(true,false);
    else if(!strcmp(argv[1],"late_new_episode")) stale_generation(false,false);
    else if(!strcmp(argv[1],"queued_role_round_trip")) stale_generation(true,true);
    else if(!strcmp(argv[1],"queued_new_episode")) stale_generation(false,true);
    else if(!strcmp(argv[1],"recovered_on_commit")) recovered_during_save();
    else if(!strcmp(argv[1],"root_isolation")) root_isolation(true);
    else if(!strcmp(argv[1],"root_loop_stall")) root_isolation(false);
    else if(!strcmp(argv[1],"full_queue")) full_queue();
    else if(!strcmp(argv[1],"reason_race")) reason_race(false);
    else if(!strcmp(argv[1],"demotion")) reason_race(true);
    else if(!strcmp(argv[1],"wait_ip")) wait_ip(false);
    else if(!strcmp(argv[1],"wait_ip_spent")) wait_ip(true);
    else if(!strcmp(argv[1],"load")) load_failures();
    else if(!strcmp(argv[1],"legacy_reasons")) legacy_reasons();
    else if(!strcmp(argv[1],"boots")) boots();
    else if(!strcmp(argv[1],"apis")) apis();
    else if(!strcmp(argv[1],"payload")) payload();
    else if(!strcmp(argv[1],"blocked_loop")) blocked_event_loop();
    else if(!strcmp(argv[1],"blocked_mesh")) blocked_mesh_action();
    else if(!strcmp(argv[1],"root_on_commit")) root_during_save(false);
    else if(!strcmp(argv[1],"root_on_reason")) root_during_save(true);
    else if(!strcmp(argv[1],"root_restart_guard")) root_restart_guard();
    else restart_failure(atoi(argv[1]));
}
''')
    binary = directory / "store"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(directory),
                    "-I", str(SOURCE), str(program), *(str(SOURCE / f) for f in (
                        "mesh_recovery_store.c", "mesh_recovery.c", "mesh_root_recovery.c", "mesh_liveness.c")),
                    "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("scenario", ["load", "legacy_reasons", "boots", "apis", "1", "2", "3", "4", "5", "6", "7", "blocked_loop", "root_restart_guard", "blocked_mesh", "root_on_commit", "root_on_reason", "full_queue", "reason_race", "demotion", "wait_ip", "wait_ip_spent", "recovered_on_commit", "root_isolation", "root_loop_stall", "late_promotion", "late_demotion", "late_role_round_trip", "late_new_episode", "queued_role_round_trip", "queued_new_episode"])
def test_storage_and_dispatch(storage_binary, scenario):
    subprocess.run([str(storage_binary), scenario], check=True)


def test_actual_recovery_formatter_stays_within_payload_limit(storage_binary):
    result = subprocess.run([str(storage_binary), "payload"], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload["no_parent_found_count"] == 4294967295
    assert payload["last_scan_times"] == -2147483648
    assert payload["recovery_stage"] == "restart_pending"
    assert payload["last_restart_reason"] == "root_link_unhealthy"
    assert len(result.stdout.encode()) < 768
