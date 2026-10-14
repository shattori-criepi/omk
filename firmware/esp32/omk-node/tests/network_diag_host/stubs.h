#pragma once
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef pthread_mutex_t portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
static _Thread_local unsigned critical_depth;
#define portENTER_CRITICAL(p) do { assert(!critical_depth); pthread_mutex_lock(p); ++critical_depth; } while(0)
#define portEXIT_CRITICAL(p) do { assert(critical_depth==1); --critical_depth; pthread_mutex_unlock(p); } while(0)
static _Atomic uint64_t fake_us;
static int64_t esp_timer_get_time(void) { return atomic_fetch_add(&fake_us,1); }
typedef uint8_t StackType_t; /* ESP-IDF Xtensa stack units are bytes. */
typedef int StaticTask_t;
static void *xTaskGetCurrentTaskHandle(void) { return (void *)(uintptr_t)pthread_self(); }
static void *xTaskCreateStatic(void (*f)(void *),const char *n,unsigned sz,void *a,unsigned p,StackType_t *s,StaticTask_t *t) {
    (void)f;(void)n;(void)a;(void)p;(void)s;assert(!critical_depth && sz==4096);return t;
}
#define pdMS_TO_TICKS(x) (x)
static void vTaskDelay(unsigned t) { (void)t; assert(0); }
#define RTC_NOINIT_ATTR
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE 1
#define ESP_ERR_NO_MEM 2
#define ESP_ERR_NVS_NOT_FOUND 3
#define ESP_ERR_NVS_NOT_ENOUGH_SPACE 4
typedef int esp_reset_reason_t;
#define ESP_RST_POWERON 1
#define ESP_RST_BROWNOUT 2
#define ESP_RST_SW 3
static int reset_kind=ESP_RST_POWERON;
static int esp_reset_reason(void) { return reset_kind; }
static unsigned boot_number;
static void esp_fill_random(void *p,size_t n) { memset(p,++boot_number,n); }
typedef struct { uint8_t app_elf_sha256[32]; } esp_app_desc_t;
static const esp_app_desc_t *esp_app_get_description(void) { static const esp_app_desc_t d={{42}}; return &d; }
typedef int nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
typedef struct { size_t free_entries; } nvs_stats_t;
static unsigned char disk_bytes[2048], staged_bytes[2048];
static size_t disk_size, staged_size, free_entries=1024;
static unsigned flash_writes;
static int flash_error;
static int nvs_get_stats(const char *n,nvs_stats_t *s) { (void)n;assert(!critical_depth);s->free_entries=free_entries;return 0; }
static int nvs_open(const char *n,int mode,nvs_handle_t *h) {
    assert(!critical_depth);assert(!strcmp(n,"omk_netdiag"));*h=1;
    return !disk_size && mode==NVS_READONLY ? ESP_ERR_NVS_NOT_FOUND : 0;
}
static int nvs_get_blob(nvs_handle_t h,const char *k,void *v,size_t *n) {
    (void)h;assert(!strcmp(k,"snapshot1"));assert(*n>=disk_size);memcpy(v,disk_bytes,disk_size);*n=disk_size;return 0;
}
static int nvs_set_blob(nvs_handle_t h,const char *k,const void *v,size_t n) {
    (void)h;assert(!critical_depth);assert(!strcmp(k,"snapshot1"));assert(n<=sizeof(staged_bytes));
    memcpy(staged_bytes,v,n);staged_size=n;++flash_writes;return flash_error;
}
static int nvs_commit(nvs_handle_t h) { (void)h;assert(!critical_depth);memcpy(disk_bytes,staged_bytes,staged_size);disk_size=staged_size;return 0; }
static void nvs_close(nvs_handle_t h) { (void)h;assert(!critical_depth); }
static char usb_output[256];
static bool usb_full;
static int usb_serial_jtag_write_bytes(const void *p,size_t n,unsigned wait) {
    assert(!critical_depth && wait==0 && n<sizeof(usb_output));
    if(usb_full) return 0;
    memcpy(usb_output,p,n);usb_output[n]=0;return n;
}
