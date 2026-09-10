"""Execute the production C discovery, SEN66 driver and manager with fake I2C/time."""
from pathlib import Path
import subprocess

SOURCE = Path(__file__).parents[1] / "src"


def test_sensor_identity_and_recovery_in_production_c(tmp_path):
    headers = {
        "esp_err.h": r"""
#pragma once
#include <stdint.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_CRC 3
#define ESP_ERR_NOT_FOUND 4
#define ESP_ERR_NO_MEM 5
#define ESP_ERR_TIMEOUT 6
static inline const char *esp_err_to_name(int e) { (void)e; return "error"; }
""",
        "driver/i2c_master.h": r"""
#pragma once
#include "esp_err.h"
typedef void *i2c_master_bus_handle_t;
typedef void *i2c_master_dev_handle_t;
typedef struct { int dev_addr_length, device_address, scl_speed_hz; } i2c_device_config_t;
typedef struct { int i2c_port, sda_io_num, scl_io_num, clk_source, glitch_ignore_cnt; struct { int enable_internal_pullup; } flags; } i2c_master_bus_config_t;
#define I2C_ADDR_BIT_LEN_7 0
#define I2C_CLK_SRC_DEFAULT 0
esp_err_t i2c_master_probe(void *, int, int);
esp_err_t i2c_master_transmit(void *, const uint8_t *, size_t, int);
esp_err_t i2c_master_receive(void *, uint8_t *, size_t, int);
esp_err_t i2c_master_bus_add_device(void *, const i2c_device_config_t *, void **);
esp_err_t i2c_master_bus_rm_device(void *);
esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *, void **);
esp_err_t i2c_del_master_bus(void *);
""",
        "esp_timer.h": "#include <stdint.h>\nint64_t esp_timer_get_time(void);\n",
        "freertos/FreeRTOS.h": "#define pdMS_TO_TICKS(x) (x)\n#define pdPASS 1\n",
        "freertos/task.h": "void vTaskDelay(unsigned);\nint xTaskCreate(void (*)(void *),const char *,unsigned,void *,unsigned,void *);\n",
        "esp_log.h": r"""
#include <stdio.h>
#define ESP_LOGI(tag, ...) do { (void)(tag); if (0) printf(__VA_ARGS__); } while (0)
#define ESP_LOGW ESP_LOGI
""",
    }
    for name, content in headers.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    harness = tmp_path / "harness.c"
    harness.write_text(r'''
#include <assert.h>
#include <string.h>
#include <math.h>
#include "sen66_sensor.h"
#include "sensor_manager.c"

static uint64_t clock_ms;
static uint8_t name[32];
static uint16_t last_command;
static unsigned mutations, identities, handles, publishes, recoveries, timeouts;
static bool present = true, connected, ready = true, bad_crc, truncated, read_error, partial, measuring;
static esp_err_t mqtt_error, logical_error;
static uint8_t crc(const uint8_t *b) {
    uint8_t c=255;
    for (int i=0;i<2;i++) { c ^= b[i]; for (int j=0;j<8;j++) c=c&128?(c<<1)^0x31:c<<1; }
    return c;
}
static void product(const char *s) { memset(name,0,32); memcpy(name,s,strlen(s)); }
esp_err_t i2c_master_probe(void *b,int a,int t) { (void)b;(void)t;assert(a==0x6b);return present?ESP_OK:ESP_ERR_NOT_FOUND; }
esp_err_t i2c_master_bus_add_device(void *b,const i2c_device_config_t *c,void **d) { (void)b;assert(c->device_address==0x6b);*d=(void *)1;handles++;return ESP_OK; }
esp_err_t i2c_master_bus_rm_device(void *d) { assert(d);assert(handles);handles--;return ESP_OK; }
esp_err_t i2c_master_transmit(void *d,const uint8_t *b,size_t n,int t) {
    (void)d;(void)t;assert(n==2);last_command=((unsigned)b[0]<<8)|b[1];
    if (last_command==0xd014) identities++;
    else if (last_command==0xd304 || last_command==0x0021 || last_command==0x0104) {
        assert(!strcmp((char *)name,"SEN66") && !bad_crc && !truncated && !partial);
        mutations++;
        if (last_command==0xd304) assert(!measuring);
        if (last_command==0x0021) measuring=true;
        if (last_command==0x0104) {
            if (!measuring) return ESP_ERR_INVALID_STATE;
            measuring=false;
        }
    }
    else assert(last_command==0x0202 || last_command==0x0300);
    return present?ESP_OK:ESP_ERR_TIMEOUT;
}
esp_err_t i2c_master_receive(void *d,uint8_t *b,size_t n,int t) {
    (void)d;(void)t;
    if (!present || truncated || (read_error && last_command!=0xd014)) return ESP_ERR_TIMEOUT;
    uint8_t data[32]={0};
    if (last_command==0xd014) { assert(n==48);memcpy(data,name,32); }
    else if (last_command==0x0202) { assert(n==3);data[1]=ready; }
    else { assert(last_command==0x0300 && n==27); for(int i=0;i<18;i+=2) data[i+1]=100; }
    for(size_t i=0;i<n/3;i++){b[i*3]=data[i*2];b[i*3+1]=data[i*2+1];b[i*3+2]=crc(data+i*2);}
    if (partial) memset(b+9,0xff,n-9);
    if(bad_crc)b[n-1]^=1;
    return ESP_OK;
}
int64_t esp_timer_get_time(void) { return clock_ms*1000; }
void vTaskDelay(unsigned ms) {clock_ms+=ms;}
esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *c,void **b){(void)c;*b=(void *)1;return ESP_OK;}
esp_err_t i2c_del_master_bus(void *b){(void)b;return ESP_OK;}
int xTaskCreate(void (*f)(void *),const char *n,unsigned s,void *a,unsigned p,void *h){(void)f;(void)n;(void)s;(void)a;(void)p;(void)h;return 1;}
void mqtt_registration_set_sen66_connected(bool v){connected=v;}
void mqtt_registration_set_sen66_diagnostics(uint32_t r,uint32_t t){recoveries=r;timeouts=t;}
esp_err_t node_registration_get_logical_id(char *s,size_t n){assert(n>10);strcpy(s,"sen66-001");return logical_error;}
esp_err_t mqtt_registration_publish_sen66(const char *id,const sen66_measurement_t *m){assert(!strcmp(id,"sen66-001"));assert(m->pm2_5_ug_m3==10);publishes++;return mqtt_error;}
static sensor_identity_t fake_result;
static unsigned fake_calls;
static sensor_identity_t fake_identify(const sensor_endpoint_t *e){(void)e;fake_calls++;return fake_result;}
static bool accepts_all(const sensor_endpoint_t *e){(void)e;return true;}
static void tick(sensor_slot_t *s){clock_ms=s->next_attempt_ms;sensor_slot_tick(s);}
int main(void) {
    sensor_endpoint_t e={.transport=SENSOR_TRANSPORT_I2C,.handle=(void *)1,.location.i2c_address=0x6b};
    const sensor_driver_t *selected=NULL;
    product("SEN66");
    assert(sensor_discover(&e,sensor_drivers,sensor_driver_count,&selected)==SENSOR_ID_EXACT);
    assert(selected && mutations==0 && handles==0);
    const char *others[]={"SEN65","SEN63C","SEN68","SEN0466","SEN66-extra","UNKNOWN",""};
    for(unsigned i=0;i<sizeof(others)/sizeof(*others);i++){
        product(others[i]);sen66_sensor_t s={0};assert(sen66_sensor_probe(e.handle,&s)!=ESP_OK);
        assert(sen66_sensor_start(e.handle,&s)==ESP_ERR_INVALID_STATE);assert(mutations==0 && handles==0);
    }
    product("SEN66"); bad_crc=true;assert(sen66_sensor_identify(e.handle)==SENSOR_ID_ERROR);bad_crc=false;
    truncated=true;assert(sen66_sensor_identify(e.handle)==SENSOR_ID_ERROR);truncated=false;
    partial=true;assert(sen66_sensor_identify(e.handle)==SENSOR_ID_ERROR);partial=false;
    memset(name,'A',32);assert(sen66_sensor_identify(e.handle)==SENSOR_ID_ERROR);
    product("SEN66");name[2]=0xff;assert(sen66_sensor_identify(e.handle)==SENSOR_ID_ERROR);
    present=false;assert(sen66_sensor_identify(e.handle)==SENSOR_ID_NO_MATCH);present=true;
    assert(mutations==0 && handles==0);
    product("SEN66");
    sen66_sensor_t swapped={0};assert(sen66_sensor_probe(e.handle,&swapped)==ESP_OK);
    product("SEN65");assert(sen66_sensor_start(e.handle,&swapped)==ESP_ERR_INVALID_STATE);
    assert(mutations==0 && handles==0);
    product("SEN66");
    sensor_driver_t other={.driver_id="future",.transport=SENSOR_TRANSPORT_I2C,.automatic_identity=true,.accepts=accepts_all,.identify=fake_identify};
    const sensor_driver_t *drivers[]={sensor_drivers[0],&other};
    fake_result=SENSOR_ID_EXACT;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_AMBIGUOUS && !selected);
    const sensor_driver_t *reverse[]={&other,sensor_drivers[0]};
    assert(sensor_discover(&e,reverse,2,&selected)==SENSOR_ID_AMBIGUOUS && !selected);
    fake_result=SENSOR_ID_AMBIGUOUS;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_AMBIGUOUS && !selected);
    fake_result=SENSOR_ID_ERROR;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_ERROR && !selected);
    fake_result=SENSOR_ID_NO_MATCH;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_EXACT);
    product("SEN0466");fake_result=SENSOR_ID_EXACT;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_EXACT && selected==&other);
    other.automatic_identity=false;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_CONFIGURATION_REQUIRED);
    unsigned calls=fake_calls;
    e.transport=SENSOR_TRANSPORT_ANALOG;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_CONFIGURATION_REQUIRED);
    e.transport=SENSOR_TRANSPORT_UART;assert(sensor_discover(&e,drivers,2,&selected)==SENSOR_ID_CONFIGURATION_REQUIRED);
    assert(fake_calls==calls && mutations==0);
    e.transport=SENSOR_TRANSPORT_I2C;
    // Actual manager + actual SEN66 adapter, including recovery and registration gating.
    sensor_slot_t slot={.endpoint=e};
    product("SEN0466");tick(&slot);assert(!connected && !slot.driver && !publishes && !mutations);
    product("SEN66");tick(&slot);assert(connected && slot.driver && mutations==3);
    logical_error=ESP_ERR_NOT_FOUND;tick(&slot);assert(publishes==0 && connected);logical_error=ESP_OK;
    mqtt_error=ESP_ERR_TIMEOUT;tick(&slot);assert(publishes==1 && connected);mqtt_error=ESP_OK;
    tick(&slot);assert(publishes==2 && connected);
    unsigned before=mutations, old_publishes=publishes, old_identities=identities;
    present=false;tick(&slot);assert(!connected && !slot.driver && handles==0 && mutations==before);
    assert(recoveries==1 && !timeouts);
    present=true;product("SEN0466");tick(&slot);assert(!connected && !slot.driver && publishes==old_publishes && mutations==before);
    product("SEN66");tick(&slot);assert(connected && identities>old_identities && mutations==before+3);
    uint64_t deadline=slot.next_attempt_ms;
    assert(deadline-clock_ms==10000);
    sensor_slot_tick(&slot);assert(slot.next_attempt_ms==deadline); // No early measurement.
    // Liveness timeout despite successful not-ready responses.
    ready=false;for(int i=0;i<7 && slot.driver;i++)tick(&slot);
    assert(!connected && timeouts==1 && recoveries==2 && handles==0);
    assert(slot.next_attempt_ms-clock_ms==60000);
    ready=true;tick(&slot);assert(connected);
    read_error=true;for(int i=0;i<3;i++)tick(&slot);assert(!connected && recoveries==3);read_error=false;
    tick(&slot);assert(connected);
    // Seamless exchange must fail identity before reading/publishing measurements.
    product("SEN65");before=mutations;old_publishes=publishes;tick(&slot);
    assert(!connected && !slot.driver && mutations==before && publishes==old_publishes && handles==0);
    return 0;
}
''')
    binary = tmp_path / "identity-test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(tmp_path), "-I", str(SOURCE),
                    str(harness), str(SOURCE / "sen66_sensor.c"), str(SOURCE / "sensor_driver.c"),
                    str(SOURCE / "sensor_drivers.c"), "-o", str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
