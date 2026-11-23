#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>

/* SDK boundary doubles only. The complete production transport/session/wire
 * sources are compiled; tests drive the registered callbacks and real queues.
 * RF, scheduler and the closed Mesh binary are not simulated. */
typedef int esp_err_t;
#define RTC_NOINIT_ATTR
#define CONFIG_OMK_RELAY_RESTART_NODE_ID "020000000042"
typedef enum { ESP_RST_POWERON=1, ESP_RST_SW=3, ESP_RST_TASK_WDT=6 } esp_reset_reason_t;
esp_reset_reason_t esp_reset_reason(void);
void esp_restart(void);
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_INVALID_SIZE 0x104
#define ESP_ERR_TIMEOUT 0x107
#define ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED 0x5003
#define ESP_ERR_MESH_ARGUMENT 0x4008
#define ESP_ERR_MESH_TIMEOUT 0x400a
#define ESP_ERR_MESH_NO_MEMORY 0x4007
const char *esp_err_to_name(int error);
void test_log(const char *tag, const char *format, ...);
#define ESP_LOGI(...) test_log(__VA_ARGS__)
#define ESP_LOGW(...) test_log(__VA_ARGS__)
#define ESP_LOGE(...) test_log(__VA_ARGS__)
typedef int portMUX_TYPE;
typedef int BaseType_t;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) (ms)
#define pdTRUE 1
#define pdPASS 1
typedef void *TaskHandle_t;
typedef struct test_queue *QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned count, size_t size);
int xQueueSend(QueueHandle_t queue, const void *value, unsigned ticks);
int xQueueOverwrite(QueueHandle_t queue, const void *value);
int xQueueReceive(QueueHandle_t queue, void *value, unsigned ticks);
void xQueueReset(QueueHandle_t queue);
int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg,
                unsigned priority, TaskHandle_t *handle);
void vTaskDelay(unsigned ticks);
int64_t esp_timer_get_time(void);
uint32_t esp_random(void);
typedef int esp_event_base_t;
typedef void (*esp_event_handler_t)(void *, esp_event_base_t, int32_t, void *);
#define ESP_EVENT_ANY_ID -1
#define MESH_EVENT 1
#define IP_EVENT 2
enum { IP_EVENT_STA_GOT_IP, IP_EVENT_STA_LOST_IP };
esp_err_t esp_event_handler_register(esp_event_base_t base, int32_t id, esp_event_handler_t fn, void *arg);
enum { ESP_MAC_WIFI_STA, ESP_MAC_WIFI_SOFTAP };
esp_err_t esp_read_mac(uint8_t *mac, int type);
typedef struct { int unused; } wifi_config_t;
#define MACSTR "%02x:%02x:%02x:%02x:%02x:%02x"
#define MAC2STR(a) (a)[0], (a)[1], (a)[2], (a)[3], (a)[4], (a)[5]
#define WIFI_PS_NONE 0
#define WIFI_AUTH_WPA2_PSK 3
esp_err_t esp_wifi_set_ps(int value);
esp_err_t esp_wifi_start(void);
typedef struct { uint8_t addr[6]; } mesh_addr_t;
typedef struct { uint8_t *data; uint16_t size; int proto, tos; } mesh_data_t;
typedef struct {
    mesh_addr_t mesh_id;
    int channel;
    struct { uint8_t ssid_len; uint8_t ssid[32], password[64]; } router;
    struct { int max_connection, nonmesh_max_connection; uint8_t password[64]; } mesh_ap;
} mesh_cfg_t;
#define MESH_INIT_CONFIG_DEFAULT() {0}
#define CONFIG_MESH_ROUTE_TABLE_SIZE 50
#define CONFIG_MESH_MAX_LAYER 6
#define MESH_MTU 1500
#define MESH_ROOT_LAYER 1
#define MESH_PROTO_BIN 0
#define MESH_TOS_P2P 0
#define MESH_TOS_DEF 2
#define MESH_DATA_NONBLOCK 1
#define MESH_DATA_P2P 2
#define MESH_DATA_TODS 4
enum { MESH_EVENT_STARTED, MESH_EVENT_STOPPED, MESH_EVENT_PARENT_CONNECTED,
       MESH_EVENT_PARENT_DISCONNECTED, MESH_EVENT_LAYER_CHANGE, MESH_EVENT_ROOT_ADDRESS,
       MESH_EVENT_NETWORK_STATE, MESH_EVENT_TODS_STATE, MESH_EVENT_VOTE_STARTED,
       MESH_EVENT_VOTE_STOPPED, MESH_EVENT_ROOT_SWITCH_REQ, MESH_EVENT_ROOT_SWITCH_ACK };
#define MESH_VOTE_REASON_ROOT_INITIATED 1
esp_err_t esp_mesh_waive_root(const void *vote, int reason);
esp_err_t esp_mesh_set_self_organized(bool enable, bool select_parent);
esp_err_t esp_mesh_connect(void);
esp_err_t esp_mesh_disconnect(void);
bool esp_mesh_get_self_organized(void);
int esp_mesh_get_routing_table_size(void);
typedef struct { uint8_t bssid[6]; } test_connected_t;
typedef struct { test_connected_t connected; uint16_t self_layer; } mesh_event_connected_t;
typedef struct { uint8_t ssid[32], ssid_len, bssid[6], reason; int8_t rssi; } mesh_event_disconnected_t;
typedef struct { int new_layer; } mesh_event_layer_change_t;
typedef struct { uint8_t addr[6]; } mesh_event_root_address_t;
typedef struct { bool is_rootless; } mesh_event_network_state_t;
typedef int mesh_event_toDS_state_t;
#define MESH_TODS_REACHABLE 1
bool esp_mesh_is_root(void);
int esp_mesh_get_layer(void);
esp_err_t esp_mesh_get_parent_bssid(mesh_addr_t *parent);
esp_err_t esp_mesh_init(void);
esp_err_t esp_mesh_allow_root_conflicts(bool allowed);
bool esp_mesh_is_root_conflicts_allowed(void);
esp_err_t esp_mesh_set_max_layer(int layer);
esp_err_t esp_mesh_set_ap_assoc_expire(int seconds);
esp_err_t esp_mesh_send_block_time(uint32_t ms);
esp_err_t esp_mesh_set_ap_authmode(int mode);
esp_err_t esp_mesh_set_config(const mesh_cfg_t *config);
esp_err_t esp_mesh_start(void);
esp_err_t esp_mesh_post_toDS_state(bool reachable);
esp_err_t esp_mesh_send(const mesh_addr_t *to, const mesh_data_t *data, int flags, const void *opt, int count);
esp_err_t esp_mesh_recv(mesh_addr_t *from, mesh_data_t *data, int timeout, int *flags, void *opt, int count);
typedef struct { int unused; } esp_netif_t;
struct netif { int unused; };
typedef esp_err_t (*esp_netif_callback_fn)(void *);
esp_err_t esp_netif_tcpip_exec(esp_netif_callback_fn fn, void *arg);
void *esp_netif_get_netif_impl(esp_netif_t *netif);
bool netif_is_link_up(const struct netif *netif);
bool dhcp_supplied_address(const struct netif *netif);
typedef struct { uint32_t addr; } esp_ip4_addr_t;
typedef struct { esp_ip4_addr_t ip, netmask, gw; } esp_netif_ip_info_t;
typedef struct { esp_netif_t *esp_netif; esp_netif_ip_info_t ip_info; bool ip_changed; } ip_event_got_ip_t;
typedef enum { ESP_NETIF_DHCP_INIT, ESP_NETIF_DHCP_STARTED, ESP_NETIF_DHCP_STOPPED } esp_netif_dhcp_status_t;
esp_err_t esp_netif_create_default_wifi_mesh_netifs(esp_netif_t **sta, esp_netif_t **ap);
esp_err_t esp_netif_dhcpc_start(esp_netif_t *netif);
esp_err_t esp_netif_dhcpc_stop(esp_netif_t *netif);
esp_err_t esp_netif_dhcpc_get_status(esp_netif_t *netif, esp_netif_dhcp_status_t *status);
esp_err_t esp_netif_get_ip_info(esp_netif_t *netif, esp_netif_ip_info_t *info);
bool esp_netif_is_netif_up(esp_netif_t *netif);
typedef void *esp_mqtt_client_handle_t;
typedef struct {
    struct { struct { const char *uri; } address; } broker;
    struct { const char *client_id; } credentials;
    struct { unsigned limit; } outbox;
    struct { int timeout_ms, reconnect_timeout_ms; } network;
    struct { unsigned size; } buffer;
    struct { unsigned keepalive; } session;
} esp_mqtt_client_config_t;
typedef struct {
    esp_mqtt_client_handle_t client;
    int current_data_offset, data_len, total_data_len, topic_len, msg_id, retain;
    const char *topic, *data;
} esp_mqtt_event_t;
typedef esp_mqtt_event_t *esp_mqtt_event_handle_t;
enum { MQTT_EVENT_ANY=-1, MQTT_EVENT_CONNECTED, MQTT_EVENT_DISCONNECTED, MQTT_EVENT_DATA, MQTT_EVENT_PUBLISHED };
esp_err_t esp_mqtt_client_disconnect(esp_mqtt_client_handle_t client);
esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *config);
esp_err_t esp_mqtt_client_register_event(esp_mqtt_client_handle_t client, int id, esp_event_handler_t fn, void *arg);
esp_err_t esp_mqtt_client_start(esp_mqtt_client_handle_t client);
esp_err_t esp_mqtt_client_stop(esp_mqtt_client_handle_t client);
esp_err_t esp_mqtt_client_destroy(esp_mqtt_client_handle_t client);
int esp_mqtt_client_subscribe(esp_mqtt_client_handle_t client, const char *topic, int qos);
int esp_mqtt_client_get_outbox_size(esp_mqtt_client_handle_t client);
int esp_mqtt_client_enqueue(esp_mqtt_client_handle_t client, const char *topic, const char *payload,
                            int length, int qos, int retain, bool store);
