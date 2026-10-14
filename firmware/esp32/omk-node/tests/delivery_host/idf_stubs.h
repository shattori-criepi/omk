#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <pthread.h>
#include <stdatomic.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_NO_MEM 3
#define ESP_ERR_INVALID_SIZE 4
#define ESP_ERR_NVS_NOT_FOUND 5
#define ESP_ERR_TIMEOUT 6
static inline const char *esp_err_to_name(int err) { (void)err; return "error"; }
#define ESP_LOGI(tag, ...) do { (void)(tag); if (0) printf(__VA_ARGS__); } while (0)
#define ESP_LOGW ESP_LOGI
#define ESP_LOGE ESP_LOGI
int64_t esp_timer_get_time(void);
void esp_fill_random(void *, size_t);
typedef pthread_mutex_t portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
#define portENTER_CRITICAL(p) assert(pthread_mutex_lock(p) == 0)
#define portEXIT_CRITICAL(p) assert(pthread_mutex_unlock(p) == 0)
typedef void *TaskHandle_t;
#define pdPASS 1
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
int xTaskCreate(void (*)(void *), const char *, unsigned, void *, unsigned, TaskHandle_t *);
void xTaskNotifyGive(TaskHandle_t);
unsigned ulTaskNotifyTake(int, unsigned);
void vTaskDelay(unsigned);

typedef void *i2c_master_bus_handle_t;
typedef void *i2c_master_dev_handle_t;
typedef struct { int i2c_port, sda_io_num, scl_io_num, clk_source, glitch_ignore_cnt;
    struct { int enable_internal_pullup; } flags; } i2c_master_bus_config_t;
#define I2C_CLK_SRC_DEFAULT 0
esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *, void **);
esp_err_t i2c_del_master_bus(void *);

typedef const char *esp_event_base_t;
typedef void (*event_handler_t)(void *, esp_event_base_t, int32_t, void *);
#define IP_EVENT "IP"
#define IP_EVENT_STA_GOT_IP 1
esp_err_t esp_event_handler_register(esp_event_base_t, int, event_handler_t, void *);
typedef struct { uint32_t addr; } esp_ip4_addr_t;
typedef struct { struct { esp_ip4_addr_t ip, gw, netmask; } ip_info; } ip_event_got_ip_t;
#define IPSTR "%u"
#define IP2STR(p) ((p)->addr)
typedef struct { int rssi; } wifi_ap_record_t;
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t *);

typedef enum { MQTT_EVENT_ANY=-1, MQTT_EVENT_CONNECTED, MQTT_EVENT_DATA,
    MQTT_EVENT_DISCONNECTED, MQTT_EVENT_ERROR, MQTT_EVENT_DELETED, MQTT_EVENT_PUBLISHED, MQTT_USER_EVENT } esp_mqtt_event_id_t;
typedef struct mock_mqtt_client *esp_mqtt_client_handle_t;
typedef struct { int error_type, esp_tls_last_esp_err, esp_tls_stack_err,
    esp_transport_sock_errno, connect_return_code; } mqtt_error_t;
typedef struct {
    void *client;
    int event_id, protocol_ver, msg_id;
    char *topic, *data;
    int topic_len, data_len, total_data_len, current_data_offset;
    mqtt_error_t *error_handle;
} esp_mqtt_event_t;
typedef esp_mqtt_event_t *esp_mqtt_event_handle_t;
struct mock_mqtt_config { void *event_loop_handle; };
struct mock_mqtt_client {
    pthread_mutex_t *api_lock;
    void *outbox;
    struct mock_mqtt_config *config;
    struct { struct { struct { int protocol_ver; } information; } connection; } mqtt_state;
    esp_mqtt_event_t event;
    atomic_int queued_events;
};
#define MQTT_PROTOCOL_V_5 5
#define MQTT_SUPPORTED_FEATURE_EVENT_LOOP 1
#define MQTT_EVENTS "MQTT"
#define portMAX_DELAY UINT32_MAX
#define MQTT_EVENT_QUEUE_SIZE CONFIG_MQTT_EVENT_QUEUE_SIZE
esp_err_t esp_event_post_to(void *, const char *, int, const void *, size_t, unsigned);
esp_err_t esp_event_loop_run(void *, unsigned);

typedef struct {
    struct { unsigned limit; } outbox;
    struct { struct { const char *uri; } address; } broker;
    struct { const char *client_id; } credentials;
    struct { struct { const char *topic, *msg; int qos, retain; } last_will; } session;
} esp_mqtt_client_config_t;
#ifndef CONFIG_MQTT_EVENT_QUEUE_SIZE
#define CONFIG_MQTT_EVENT_QUEUE_SIZE 1
#endif
esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *);
esp_err_t esp_mqtt_client_start(void *);
esp_err_t esp_mqtt_client_reconnect(void *);
esp_err_t esp_mqtt_client_register_event(void *, int, event_handler_t, void *);
int esp_mqtt_client_publish(void *, const char *, const char *, int, int, int);
int esp_mqtt_client_enqueue(void *, const char *, const char *, int, int, int, bool);
int esp_mqtt_client_get_outbox_size(void *);
int esp_mqtt_client_subscribe(void *, const char *, int);
esp_err_t esp_mqtt_dispatch_custom_event(esp_mqtt_client_handle_t, esp_mqtt_event_t *);
