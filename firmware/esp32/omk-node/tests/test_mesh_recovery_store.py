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
    dispatch = network[network.index("static void run_recovery_action("):network.index("static void try_recovery(")]
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
static omk_mesh_recovery_record_t durable, pending;
static bool exists;
static int failure, opens, gets, commits, closes;
static omk_mesh_recovery_t recovery;
static omk_mesh_recovery_record_t recovery_record;
static omk_mesh_root_recovery_t root_recovery;
static bool root, parent_connected, is_rootless, role_change;
static unsigned selections, enables, connects, votes, restarts, reasons;
static esp_err_t api_result;
#define TAG "test"
#define ESP_LOGI(tag, ...) do { (void)(tag); } while (0)
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
    durable=pending; exists=true; return ESP_OK;
}
void nvs_close(nvs_handle_t h) { (void)h; ++closes; }
static bool esp_mesh_is_root(void) { return root; }
static esp_err_t esp_mesh_set_self_organized(bool enable,bool select) {
    assert(enable); ++enables;
    if (select) ++selections;
    if (role_change) root=!root;
    return api_result;
}
static esp_err_t esp_mesh_connect(void) {
    assert(root && !parent_connected); ++connects; return api_result;
}
static void try_root_recovery(void) { ++votes; }
static void mesh_network_log_diagnostics(const char *reason, uint32_t code) {
    assert(code==0 && strstr(reason,"timeout"));
}
static void boot_diagnostics_record_restart_reason(const char *reason) {
    assert(!strcmp(reason,"mesh_parent_loss_timeout") || !strcmp(reason,"mesh_mqtt_liveness_timeout"));
    ++reasons;
}
static void esp_restart(void) {
    assert(exists && durable.spent==1 && gets>0 && commits>0 && closes>=2);
    assert(reasons==restarts+1); ++restarts;
}
static uint32_t uptime_seconds(void) { return UINT32_MAX; }
static uint32_t uptime_milliseconds(void) { return UINT32_MAX; }
static esp_err_t mqtt_registration_publish_mesh_recovery_status(const char *payload) {
    assert(strlen(payload)<768 && strlen(payload)<1152); puts(payload); return ESP_OK;
}
''' + dispatch + formatter + r'''
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
static void restart_failure(int f) {
    failure=0; assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    assert(recovery_record.spent==0);
    mesh_recovery_init(&recovery,false,1); recovery.reason=OMK_RECOVERY_PARENT_LOSS;
    failure=f;
    run_recovery_action(OMK_RECOVERY_RESTART,600000);
    assert(restarts==0 && reasons==0 && recovery.restart_spent);
    failure=0;
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
    if(f>=4) assert(recovery_record.spent==1); /* Commit succeeded before read-back failure. */
}
static void boots(void) {
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK && !recovery_record.spent);
    mesh_recovery_init(&recovery,false,1); recovery.reason=OMK_RECOVERY_NO_PARENT;
    run_recovery_action(OMK_RECOVERY_RESTART,600000);
    assert(restarts==1);
    for (int boot=0;boot<4;boot++) {
        assert(mesh_recovery_store_load(&recovery_record)==ESP_OK);
        assert(recovery_record.spent==1 && recovery_record.restart_count==1);
        mesh_recovery_init(&recovery,recovery_record.spent,1);
        omk_mesh_recovery_input_t in={.started=true};
        for(uint32_t t=0;t<3600000;t+=30000) {
            omk_mesh_recovery_action_t a=mesh_recovery_choose(&recovery,t,&in,&root_recovery);
            assert(a!=OMK_RECOVERY_RESTART && a!=OMK_RECOVERY_REARM);
            run_recovery_action(a,t);
        }
        assert(restarts==1);
    }
    omk_mesh_recovery_input_t in={.started=true,.parent_connected=true,.has_ip=true,.mqtt_connected=true};
    assert(mesh_recovery_choose(&recovery,4000000,&in,&root_recovery)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&recovery,4899999,&in,&root_recovery)==OMK_RECOVERY_NONE);
    assert(mesh_recovery_choose(&recovery,4900000,&in,&root_recovery)==OMK_RECOVERY_REARM);
    failure=3;
    run_recovery_action(OMK_RECOVERY_REARM,4900000);
    assert(recovery.restart_spent && durable.spent);
    failure=0;
    run_recovery_action(OMK_RECOVERY_REARM,4900000);
    assert(!recovery.restart_spent);
    assert(mesh_recovery_store_load(&recovery_record)==ESP_OK && !recovery_record.spent);
    assert(recovery_record.restart_count==1 && recovery_record.reason==OMK_RECOVERY_NO_PARENT);
}
static void apis(void) {
    run_recovery_action(OMK_RECOVERY_CHILD_RESELECT,0);
    assert(selections==1 && enables==1 && !connects && recovery.explicit_accepted);
    root=true;
    run_recovery_action(OMK_RECOVERY_CHILD_RESELECT,30000); /* Became root since decision. */
    assert(enables==1);
    run_recovery_action(OMK_RECOVERY_ROUTER_RECONNECT,60000);
    assert(enables==2 && selections==1 && connects==1);
    root=false;
    run_recovery_action(OMK_RECOVERY_ROUTER_RECONNECT,90000);
    assert(enables==2);
    root=true; api_result=ESP_FAIL;
    run_recovery_action(OMK_RECOVERY_ROUTER_RECONNECT,120000);
    assert(enables==3 && connects==1); /* Failed enable cannot call connect. */
    api_result=ESP_OK; role_change=true;
    run_recovery_action(OMK_RECOVERY_ROUTER_RECONNECT,150000);
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
int main(int argc,char **argv) {
    assert(argc==2); (void)esp_err_to_name(ESP_OK);
    if(!strcmp(argv[1],"load")) load_failures();
    else if(!strcmp(argv[1],"boots")) boots();
    else if(!strcmp(argv[1],"apis")) apis();
    else if(!strcmp(argv[1],"payload")) payload();
    else restart_failure(atoi(argv[1]));
}
''')
    binary = directory / "store"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(directory),
                    "-I", str(SOURCE), str(program), *(str(SOURCE / f) for f in (
                        "mesh_recovery_store.c", "mesh_recovery.c", "mesh_root_recovery.c", "mesh_liveness.c")),
                    "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("scenario", ["load", "boots", "apis", "1", "2", "3", "4", "5", "6", "7"])
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
