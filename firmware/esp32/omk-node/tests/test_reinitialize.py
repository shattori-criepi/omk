"""Execute production selective reset/import with simulated persistent storage.

Inject a power cut after each persistent mutation, then boot again. No USB,
real NVS, Wi-Fi credentials or hardware is touched.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).parents[1] / 'src'


@pytest.fixture(scope='module')
def reset_binary(tmp_path_factory):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler required')
    main = (SOURCE/'main.c').read_text()
    factory = main[main.index('#define FACTORY_MAGIC'):main.index('void app_main')]
    credentials = (SOURCE/'gateway_credentials.c').read_text()
    clear = credentials[credentials.index('esp_err_t gateway_credentials_clear(void)'):credentials.index('esp_err_t gateway_credentials_migrate_legacy')]
    registration = (SOURCE/'node_registration.c').read_text()
    registration = registration[registration.index('esp_err_t node_registration_clear(void)'):registration.index('esp_err_t node_registration_clear_for_development')]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NVS_NOT_FOUND -2
#define NVS_READONLY 0
#define NVS_READWRITE 1
#define ESP_PARTITION_TYPE_DATA 1
#define NODE_NVS_NAMESPACE "omk"
#define NODE_NVS_PROVISIONING_POP_KEY "prov_pop"
#define OMK_GATEWAY_CREDENTIALS_NAMESPACE "omk_net"
#define OMK_GATEWAY_CREDENTIALS_KEY "gw_cred"
#define OMK_NVS_REGISTERED_KEY "registered"
#define OMK_NVS_LOGICAL_ID_KEY "logical_id"
#define WIFI_IF_STA 0
#define ESP_ERROR_CHECK(call) do { if ((call) != ESP_OK) longjmp(restart, 2); } while (0)
typedef int esp_err_t;
typedef int nvs_handle_t;
typedef struct { int size; } esp_partition_t;
typedef struct { unsigned char bytes[100]; } gateway_credentials_record_t;
typedef struct { struct { unsigned char ssid[32], password[64]; bool bssid_set; } sta; } wifi_config_t;
static wifi_config_t sta;
static jmp_buf restart;
static int mutations, fail_at;
static unsigned char factory_data[4096];
static esp_partition_t partition = {4096};
struct cell { const char *key; unsigned char data[128]; size_t length; bool present; };
static struct cell cells[] = {{.key="prov_pop"}, {.key="registered"}, {.key="logical_id"}, {.key="gw_cred"}, {.key="reset_sta"}, {.key="boot_count"}};
static void cut(void) { if (++mutations == fail_at) longjmp(restart, 1); }
static struct cell *cell(const char *key) {
    for (unsigned i=0; i<sizeof(cells)/sizeof(cells[0]); i++) if (!strcmp(cells[i].key,key)) return &cells[i];
    assert(false); return NULL;
}
static int nvs_open(const char *name, int mode, nvs_handle_t *nvs) {
    (void)name; (void)mode; *nvs = 1; return ESP_OK;
}
static void nvs_close(nvs_handle_t nvs) { (void)nvs; }
static int nvs_set_blob(nvs_handle_t nvs, const char *key, const void *value, size_t length) {
    (void)nvs; struct cell *c=cell(key); assert(length<=sizeof(c->data));
    memcpy(c->data,value,length); c->length=length; c->present=true; cut(); return ESP_OK;
}
static int nvs_get_blob(nvs_handle_t nvs, const char *key, void *value, size_t *length) {
    (void)nvs; struct cell *c=cell(key); if(!c->present) return ESP_ERR_NVS_NOT_FOUND;
    if(value) { assert(*length>=c->length); memcpy(value,c->data,c->length); }
    *length=c->length; return ESP_OK;
}
static int nvs_set_u8(nvs_handle_t nvs, const char *key, uint8_t value) { return nvs_set_blob(nvs,key,&value,1); }
static int nvs_get_u8(nvs_handle_t nvs, const char *key, uint8_t *value) { size_t length=1; return nvs_get_blob(nvs,key,value,&length); }
static int nvs_get_str(nvs_handle_t nvs, const char *key, char *value, size_t *length) { return nvs_get_blob(nvs,key,value,length); }
static int nvs_erase_key(nvs_handle_t nvs, const char *key) {
    (void)nvs; if(!cell(key)->present) return ESP_ERR_NVS_NOT_FOUND;
    cell(key)->present=false; cut(); return ESP_OK;
}
static int nvs_commit(nvs_handle_t nvs) { (void)nvs; cut(); return ESP_OK; }
static const esp_partition_t *esp_partition_find_first(int type, int subtype, const char *name) {
    assert(type==ESP_PARTITION_TYPE_DATA && subtype==0x40 && !strcmp(name,"factory_secret")); return &partition;
}
static int esp_partition_read(const esp_partition_t *part, int offset, void *value, size_t length) {
    assert(part==&partition && offset==0); memcpy(value,factory_data,length); return ESP_OK;
}
static int esp_partition_erase_range(const esp_partition_t *part, int offset, int size) {
    assert(part==&partition && offset==0 && size==4096); memset(factory_data,0xff,sizeof(factory_data)); cut(); return ESP_OK;
}
static int esp_wifi_set_config(int interface, const wifi_config_t *config) { assert(interface==0); sta=*config; cut(); return ESP_OK; }
static int esp_wifi_get_config(int interface, wifi_config_t *config) { assert(interface==0); *config=sta; return ESP_OK; }
''' + registration + clear + factory + r'''
int main(int argc, char **argv) {
    assert(argc==3);
    unsigned char old_pop[32], new_pop[32]; memset(old_pop,0xab,32); memset(new_pop,0xcd,32);
    nvs_set_blob(1,"prov_pop",old_pop,32); nvs_set_u8(1,"registered",1);
    nvs_set_blob(1,"logical_id","old-sen66",10); nvs_set_blob(1,"gw_cred","old wifi",9);
    nvs_set_u8(1,"boot_count",77);
    strcpy((char *)sta.sta.ssid,"old-ssid"); strcpy((char *)sta.sta.password,"old-password"); sta.sta.bssid_set=true;
    memcpy(factory_data,argv[1],4); memcpy(factory_data+4,new_pop,32);
    fail_at=atoi(argv[2]); mutations=0;
    if(setjmp(restart)==0) { import_factory_pop(); ESP_ERROR_CHECK(gateway_credentials_clear_legacy_for_setup()); }
    fail_at=0;
    import_factory_pop(); ESP_ERROR_CHECK(gateway_credentials_clear_legacy_for_setup());
    assert(cell("prov_pop")->present && !memcmp(cell("prov_pop")->data,new_pop,32));
    assert(cell("boot_count")->present && cell("boot_count")->data[0]==77);
    if(!strcmp(argv[1],"OMKR")) {
        assert(!cell("logical_id")->present && !cell("registered")->present && !cell("gw_cred")->present);
        assert(!cell("reset_sta")->present);
        assert(!sta.sta.ssid[0] && !sta.sta.password[0] && !sta.sta.bssid_set);
    } else {
        assert(cell("logical_id")->present && cell("registered")->present && cell("gw_cred")->present);
        assert(!strcmp((char *)sta.sta.ssid,"old-ssid"));
    }
    assert(factory_data[0]==0xff);
    int completed=mutations;
    import_factory_pop(); // A later ordinary boot must not repeat any reset.
    assert(mutations==completed);
    puts("ok");
}
'''
    root = tmp_path_factory.mktemp('reinitialize-c')
    (root/'reset.c').write_text(harness)
    binary = root/'reset'
    subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', str(root/'reset.c'), '-o', str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize('magic', ['OMKP', 'OMKR'])
@pytest.mark.parametrize('power_cut', range(0, 15))
def test_factory_import_and_reinitialize_are_selective_and_restartable(reset_binary, magic, power_cut):
    result = subprocess.run([str(reset_binary), magic, str(power_cut)], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == 'ok'


def test_reset_precedes_network_start_and_clears_broker_registration_before_subscription():
    main = (SOURCE/'main.c').read_text()
    assert main.index('    import_factory_pop();') < main.index('wifi_station_prepare(&has_wifi_credentials)')
    wifi = (SOURCE/'wifi_station.c').read_text()
    assert wifi.index('gateway_credentials_clear_legacy_for_setup()') < wifi.index('gateway_credentials_migrate_legacy(')
    mqtt = (SOURCE/'mqtt_registration.c').read_text()
    connected = mqtt[mqtt.index('case MQTT_EVENT_CONNECTED:'):mqtt.index('case MQTT_EVENT_DATA:')]
    assert connected.index('clear_retained_registration_config();') < connected.index('esp_mqtt_client_subscribe(')
    assert connected.index('clear_retained_registration_ack();') < connected.index('publish_registration_status();')
    assert 'if (!registration_is_persisted())' in connected
