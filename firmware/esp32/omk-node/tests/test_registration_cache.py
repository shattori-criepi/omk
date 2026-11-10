"""Execute atomic registration persistence and coherent cache under failed IO."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]


def test_atomic_revision_and_cache(tmp_path):
    for name in ("esp_err.h", "nvs.h", "freertos/FreeRTOS.h"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#include "platform.h"\n')
    (tmp_path / "platform.h").write_text(r'''
#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <pthread.h>
typedef int esp_err_t;
typedef int nvs_handle_t;
typedef pthread_mutex_t portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
#define portENTER_CRITICAL(p) pthread_mutex_lock(p)
#define portEXIT_CRITICAL(p) pthread_mutex_unlock(p)
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG -2
#define ESP_ERR_INVALID_STATE -3
#define ESP_ERR_NVS_NOT_FOUND -4
#define ESP_ERR_NVS_INVALID_LENGTH -5
#define NVS_READONLY 0
#define NVS_READWRITE 1
static pthread_mutex_t flash=PTHREAD_MUTEX_INITIALIZER;
static char legacy_id[49]="old-id";
static uint8_t legacy_registered=1;
static uint8_t persisted[64], pending[64];
static bool have_blob, have_pending, corrupt_readback, namespace_missing;
static unsigned reads,writes,commits;
static int fail_set,fail_commit;
static int nvs_open(const char *ns,int mode,int *h) {
 (void)ns;if(namespace_missing && mode==NVS_READONLY)return ESP_ERR_NVS_NOT_FOUND;
 pthread_mutex_lock(&flash);*h=1;return 0;
}
static void nvs_close(int h) {(void)h;have_pending=false;pthread_mutex_unlock(&flash);}
static int nvs_get_str(int h,const char *k,char *out,size_t *size) {
 (void)h;(void)k;++reads;if(!legacy_id[0])return ESP_ERR_NVS_NOT_FOUND;
 if(*size<=strlen(legacy_id))return ESP_ERR_NVS_INVALID_LENGTH;
 strcpy(out,legacy_id);*size=strlen(legacy_id)+1;return 0;
}
static int nvs_get_u8(int h,const char *k,uint8_t *v) {(void)h;(void)k;++reads;*v=legacy_registered;return 0;}
static int nvs_get_blob(int h,const char *k,void *out,size_t *size) {
 (void)h;assert(!strcmp(k,"reg_state"));++reads;if(!have_blob)return ESP_ERR_NVS_NOT_FOUND;
 if(*size<64)return ESP_ERR_NVS_INVALID_LENGTH;memcpy(out,persisted,64);*size=64;
 if(corrupt_readback)((uint8_t*)out)[0]^=1;return 0;
}
static int nvs_set_blob(int h,const char *k,const void *data,size_t size) {
 (void)h;assert(!strcmp(k,"reg_state")&&size==64);++writes;
 if(fail_set)return ESP_FAIL;memcpy(pending,data,64);have_pending=true;return 0;
}
static int nvs_commit(int h) {
 (void)h;++commits;
 /* Both old and new durable outcomes are legal across a failed flash commit. */
 if(have_pending && fail_commit!=1){memcpy(persisted,pending,64);have_blob=true;}
 return fail_commit?ESP_FAIL:ESP_OK;
}
''')
    (tmp_path / "test.c").write_text(r'''
#include "node_registration.c"
static void check(uint32_t revision,const char *expected) {
 char id[49];uint32_t r=UINT32_MAX;
 assert(node_registration_snapshot(id,sizeof(id),&r)==0);
 assert(r==revision&&!strcmp(id,expected));
}
static void reboot(void) {
 initialized=false;cached_error=ESP_ERR_INVALID_STATE;memset(&cached_record,0,sizeof(cached_record));
 node_registration_init_cache();
}
static void *reader(void *arg) {
 (void)arg;for(unsigned i=0;i<10000;i++)check(0,"old-id");return NULL;
}
int main(void) {
 char id[49];uint8_t state;
 assert(node_registration_get_logical_id(id,sizeof(id))==ESP_ERR_INVALID_STATE);
 node_registration_init_cache();check(0,"old-id");assert(writes==0);
 unsigned initial=reads;pthread_mutex_lock(&flash);pthread_t thread;
 assert(!pthread_create(&thread,NULL,reader,NULL));assert(!pthread_join(thread,NULL));
 pthread_mutex_unlock(&flash);assert(reads==initial);
 assert(node_registration_get_logical_id(id,2)==ESP_ERR_NVS_INVALID_LENGTH);
 assert(node_registration_apply(0,"old-id")==0&&writes==0);
 assert(node_registration_apply(0,"different")==ESP_ERR_INVALID_STATE&&writes==0);
 assert(node_registration_apply(1,"new-id")==0);check(1,"new-id");assert(writes==1&&commits==1);
 assert(node_registration_apply(1,"new-id")==0&&writes==1);
 assert(node_registration_apply(1,NULL)==ESP_ERR_INVALID_STATE&&writes==1);
 assert(node_registration_apply(0,"old-id")==ESP_ERR_INVALID_STATE&&writes==1);
 assert(node_registration_apply(2,NULL)==0);check(2,"");reboot();check(2,"");
 assert(node_registration_get_logical_id(id,sizeof(id))==ESP_ERR_NVS_NOT_FOUND);
 assert(node_registration_get_provisioning_state(true,&state)==0&&state==OMK_NODE_PROVISIONING_STATE_PROVISIONED);
 assert(node_registration_apply(1,"old-id")==ESP_ERR_INVALID_STATE);
 fail_set=1;assert(node_registration_apply(3,"failed")==ESP_FAIL);
 assert(node_registration_get_logical_id(id,sizeof(id))==ESP_FAIL);
 fail_set=0;assert(node_registration_apply(3,"retry")==0);check(3,"retry");
 fail_commit=1;assert(node_registration_apply(4,"not-durable")==ESP_FAIL);
 fail_commit=0;reboot();check(3,"retry");
 fail_commit=2;assert(node_registration_apply(4,"durable")==ESP_FAIL);
 fail_commit=0;reboot();check(4,"durable");initial=writes;
 assert(node_registration_apply(4,"durable")==0&&writes==initial);
 corrupt_readback=true;assert(node_registration_apply(5,"verified-later")==ESP_FAIL);
 assert(node_registration_snapshot(id,sizeof(id),&(uint32_t){0})==ESP_FAIL);
 corrupt_readback=false;reboot();check(5,"verified-later");
 assert(begin_write());assert(node_registration_apply(6,"busy")==ESP_ERR_INVALID_STATE);end_write();
 assert(node_registration_clear()==0);check(6,"");
 assert(node_registration_save("usb-id")==0);check(7,"usb-id");
 assert(node_registration_apply(UINT32_MAX,NULL)==0);assert(node_registration_clear()==ESP_ERR_INVALID_STATE);
 have_blob=false;initialized=false;cached_error=ESP_ERR_INVALID_STATE;
 assert(node_registration_clear()==0);check(1,"");reboot();check(1,""); // legacy must not resurrect
 persisted[0]^=1;reboot();assert(node_registration_get_logical_id(id,sizeof(id))==ESP_FAIL);
 initial=writes;assert(node_registration_apply(8,"bad")==ESP_FAIL&&writes==initial);
 have_blob=false;namespace_missing=true;initialized=false;node_registration_init_cache();check(0,"");
 return 0;
}
''')
    binary = tmp_path / "cache-test"
    result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation", "-pthread",
                             "-fsanitize=undefined", "-I", str(tmp_path), "-I", str(ROOT / "src"),
                             str(tmp_path / "test.c"), "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(binary)], check=True, timeout=10)
