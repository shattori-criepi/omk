#include "mesh_tx_policy.h"
#include "mesh_netif.h"
#include "network_diagnostics.h"

#include <stdlib.h>
#include <string.h>

#include "dhcpserver/dhcpserver.h"
#include "esp_log.h"
#include "esp_mesh.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "lwip/opt.h"

#if !CONFIG_LWIP_L2_TO_L3_COPY || LWIP_TCPIP_CORE_LOCKING || LWIP_TCPIP_CORE_LOCKING_INPUT
#error "Mesh RX lifetime requires copied input and FIFO tcpip mailbox IPC"
#endif

#define MESH_NETIF_RX_SIZE 1560
#define MESH_MAC_LENGTH 6

typedef struct mesh_netif_driver {
    esp_netif_driver_base_t base;
    uint8_t station_mac[MESH_MAC_LENGTH];
} *mesh_netif_driver_t;

static const char *TAG = "omk-mesh-netif";
static const esp_netif_ip_info_t mesh_subnet = {
    .ip = {.addr = ESP_IP4TOADDR(10, 0, 0, 1)},
    .gw = {.addr = ESP_IP4TOADDR(10, 0, 0, 1)},
    .netmask = {.addr = ESP_IP4TOADDR(255, 255, 0, 0)},
};
static esp_netif_t *station_netif;
static esp_netif_t *mesh_ap_netif;
static bool receive_task_running;
/* Initialized once by main, before Wi-Fi/events/RX start. Lifecycle entry points
 * run on main/default event loop, never on tcpip_thread. RX never holds this
 * across esp_mesh_recv. lwIP TX/free callbacks MUST NOT take it.
 * Lock order: lifecycle -> SDK synchronous tcpip IPC; callback -> tx_queue_lock.
 * See docs/mesh-netif-lifecycle.md for the asynchronous input/teardown contract. */
static StaticSemaphore_t lifecycle_mutex_storage;
static SemaphoreHandle_t lifecycle_mutex;
static bool station_is_child;
static bool station_connected;
static bool stopped;
static uint32_t rx_generation;

static void lifecycle_enter(void) { xSemaphoreTake(lifecycle_mutex, portMAX_DELAY); }
static void lifecycle_leave(void) { xSemaphoreGive(lifecycle_mutex); }

static mesh_addr_t routing_table[CONFIG_MESH_ROUTE_TABLE_SIZE];
static mesh_netif_diagnostics_t diagnostics;
static portMUX_TYPE tx_policy_lock = portMUX_INITIALIZER_UNLOCKED;
static omk_mesh_tx_policy_t tx_policy;

bool mesh_netif_allow_data(void) {
    portENTER_CRITICAL(&tx_policy_lock);
    bool allowed = mesh_tx_policy_allow(&tx_policy, (uint32_t)(esp_timer_get_time() / 1000));
    portEXIT_CRITICAL(&tx_policy_lock);
    return allowed;
}

static uint32_t uptime_seconds(void) {
    return (uint32_t)(esp_timer_get_time() / 1000000);
}

static esp_err_t record_mesh_send_result(esp_err_t err) {
    if (err == ESP_OK) {
        diagnostics.tx_success_count++;
        diagnostics.last_tx_success_uptime_s = uptime_seconds();
    } else {
        diagnostics.tx_failure_count++;
    }
    return err;
}

static esp_err_t observed_mesh_send(unsigned op, const mesh_addr_t *to,
                                    const mesh_data_t *data, int flags) {
    uint32_t token = network_diag_begin(op);
    esp_err_t err = esp_mesh_send(to, data, flags, NULL, 0);
    portENTER_CRITICAL(&tx_policy_lock);
    mesh_tx_policy_result(&tx_policy, (uint32_t)(esp_timer_get_time() / 1000),
                         err == ESP_ERR_MESH_NO_MEMORY || err == ESP_ERR_MESH_TIMEOUT,
                         err == ESP_OK);
    portEXIT_CRITICAL(&tx_policy_lock);
    network_diag_end(token, err, err != ESP_OK);
    return err;
}

static const char *dhcp_status_name(esp_netif_dhcp_status_t status) {
    switch (status) {
    case ESP_NETIF_DHCP_INIT:
        return "init";
    case ESP_NETIF_DHCP_STARTED:
        return "started";
    case ESP_NETIF_DHCP_STOPPED:
        return "stopped";
    default:
        return "unknown";
    }
}

static void free_mesh_buffer(void *handle, void *buffer) {
    (void)handle;
    free(buffer);
}

/* Copy Ethernet frames out of lwIP immediately. A single owner calls the
 * non-reentrant Mesh API; radio waits must never hold up the TCP/IP thread.
 * Fixed pool bounds memory; full queues fail admission and TCP can retry.
 * No caller buffers or netif/driver pointers survive the callback. */
#define MESH_TX_SLOTS 4
#define MESH_TX_MAX_AGE_US 10000000ULL
static struct {
    uint8_t bytes[MESH_NETIF_RX_SIZE], station_mac[MESH_MAC_LENGTH];
    size_t length;
    uint64_t queued_us;
    uint32_t generation;
    bool root, occupied;
} tx_frames[MESH_TX_SLOTS];
static portMUX_TYPE tx_queue_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t tx_generation;
static QueueHandle_t tx_queue;
static StaticQueue_t tx_queue_control;
static uint8_t tx_queue_storage[MESH_TX_SLOTS * sizeof(unsigned)];

static void invalidate_tx(void) {
    portENTER_CRITICAL(&tx_queue_lock);
    ++tx_generation;
    portEXIT_CRITICAL(&tx_queue_lock);
}

static bool tx_frame_current(unsigned index) {
    portENTER_CRITICAL(&tx_queue_lock);
    bool current = tx_frames[index].generation == tx_generation;
    portEXIT_CRITICAL(&tx_queue_lock);
    return current && tx_frames[index].root == esp_mesh_is_root() &&
           (uint64_t)esp_timer_get_time() - tx_frames[index].queued_us < MESH_TX_MAX_AGE_US;
}

static void send_queued_frame(unsigned index) {
    if (!tx_frame_current(index)) return;
    mesh_data_t data = {.data = tx_frames[index].bytes, .size = tx_frames[index].length,
        .proto = tx_frames[index].root ? MESH_PROTO_STA : MESH_PROTO_AP, .tos = MESH_TOS_P2P};
    if (!tx_frames[index].root) {
        (void)record_mesh_send_result(observed_mesh_send(ND_MESH_UP, NULL, &data, MESH_DATA_TODS));
        return;
    }
    static const uint8_t broadcast[MESH_MAC_LENGTH] = {255,255,255,255,255,255};
    mesh_addr_t destination;
    memcpy(destination.addr, data.data, MESH_MAC_LENGTH);
    if (memcmp(destination.addr, broadcast, MESH_MAC_LENGTH) != 0) {
        (void)record_mesh_send_result(observed_mesh_send(ND_MESH_DOWN, &destination, &data, MESH_DATA_P2P));
        return;
    }
    int count = 0;
    if (esp_mesh_get_routing_table(routing_table, sizeof(routing_table), &count) != ESP_OK) return;
    if (count <= 0 || count > CONFIG_MESH_ROUTE_TABLE_SIZE) return;
    /* A slow first peer must not starve later peers on every ARP/DHCP frame. */
    static unsigned broadcast_cursor;
    unsigned first = broadcast_cursor % (unsigned)count;
    for (int n = 0; n < count && tx_frame_current(index); ++n) {
        unsigned i = (first + (unsigned)n) % (unsigned)count;
        broadcast_cursor = (i + 1) % (unsigned)count;
        if (memcmp(routing_table[i].addr, tx_frames[index].station_mac, MESH_MAC_LENGTH) != 0)
            (void)record_mesh_send_result(observed_mesh_send(ND_MESH_BROADCAST, &routing_table[i],
                                                           &data, MESH_DATA_P2P));
    }
}

static void transmit_task(void *argument) {
    (void)argument;
    for (;;) {
        unsigned index;
        if (xQueueReceive(tx_queue, &index, portMAX_DELAY) != pdTRUE) continue;
        send_queued_frame(index);
        portENTER_CRITICAL(&tx_queue_lock);
        tx_frames[index].occupied = false;
        portEXIT_CRITICAL(&tx_queue_lock);
    }
}

static esp_err_t queue_frame(void *handle, void *buffer, size_t length, bool root) {
    if (!tx_queue) return ESP_ERR_INVALID_STATE;
    if (!buffer || length < MESH_MAC_LENGTH || length > MESH_NETIF_RX_SIZE)
        return ESP_ERR_INVALID_SIZE;
    portENTER_CRITICAL(&tx_queue_lock);
    unsigned index;
    for (index = 0; index < MESH_TX_SLOTS && tx_frames[index].occupied; ++index) {}
    if (index == MESH_TX_SLOTS) {
        portEXIT_CRITICAL(&tx_queue_lock);
        return ESP_ERR_NO_MEM;
    }
    tx_frames[index].occupied = true;
    tx_frames[index].generation = tx_generation;
    portEXIT_CRITICAL(&tx_queue_lock);
    memcpy(tx_frames[index].bytes, buffer, length);
    if (root) memcpy(tx_frames[index].station_mac,
                     ((mesh_netif_driver_t)handle)->station_mac, MESH_MAC_LENGTH);
    tx_frames[index].length = length;
    tx_frames[index].root = root;
    tx_frames[index].queued_us = esp_timer_get_time();
    if (xQueueSend(tx_queue, &index, 0) == pdTRUE) return ESP_OK;
    portENTER_CRITICAL(&tx_queue_lock);
    tx_frames[index].occupied = false;
    portEXIT_CRITICAL(&tx_queue_lock);
    return ESP_ERR_NO_MEM;
}

static esp_err_t transmit_root_ap(void *handle, void *buffer, size_t length) {
    return queue_frame(handle, buffer, length, true);
}

static esp_err_t transmit_root_ap_wrap(void *handle, void *buffer, size_t length, void *netstack_buffer) {
    (void)netstack_buffer;
    return transmit_root_ap(handle, buffer, length);
}

static esp_err_t transmit_child_sta(void *handle, void *buffer, size_t length) {
    return queue_frame(handle, buffer, length, false);
}

static esp_err_t transmit_child_sta_wrap(void *handle, void *buffer, size_t length, void *netstack_buffer) {
    (void)netstack_buffer;
    return transmit_child_sta(handle, buffer, length);
}

static esp_err_t attach_root_ap_driver(esp_netif_t *netif, void *argument) {
    mesh_netif_driver_t driver = argument;
    driver->base.netif = netif;
    esp_netif_driver_ifconfig_t config = {
        .handle = driver, .transmit = transmit_root_ap, .transmit_wrap = transmit_root_ap_wrap,
        .driver_free_rx_buffer = free_mesh_buffer,
    };
    return esp_netif_set_driver_config(netif, &config);
}

static esp_err_t attach_child_sta_driver(esp_netif_t *netif, void *argument) {
    mesh_netif_driver_t driver = argument;
    driver->base.netif = netif;
    esp_netif_driver_ifconfig_t config = {
        .handle = driver, .transmit = transmit_child_sta, .transmit_wrap = transmit_child_sta_wrap,
        .driver_free_rx_buffer = free_mesh_buffer,
    };
    return esp_netif_set_driver_config(netif, &config);
}

static mesh_netif_driver_t create_driver(bool root_ap) {
    mesh_netif_driver_t driver = calloc(1, sizeof(*driver));
    if (driver != NULL)
        driver->base.post_attach = root_ap ? attach_root_ap_driver : attach_child_sta_driver;
    return driver;
}

/* Stop drains earlier tcpip input, stops DHCP and removes lwIP references.
 * Destroy is synchronous on that SAME FIFO mailbox. Keep the custom driver
 * alive through both calls: queued input may invoke TX after receive returns.
 * The default Wi-Fi detach owns RX unregistration and its driver's free. */
static void dispose_netif(esp_netif_t *netif, mesh_netif_driver_t driver, bool wifi) {
    if (netif == NULL) return;
    esp_netif_action_stop(netif, NULL, 0, NULL);
    if (wifi) {
        esp_err_t err = esp_wifi_clear_default_wifi_driver_and_handlers(netif);
        if (err != ESP_OK) ESP_LOGE(TAG, "Default Wi-Fi detach failed: %s", esp_err_to_name(err));
    }
    esp_netif_destroy(netif);
    free(driver);
    /* Input/DHCP teardown may have emitted frames after the entry invalidation.
     * Callbacks are now drained; those old-netif frames must expire as well. */
    invalidate_tx();
}

static void destroy_station(void) {
    esp_netif_t *netif = station_netif;
    station_netif = NULL;
    station_connected = false;
    if (netif != NULL) {
        mesh_netif_driver_t driver = station_is_child ? esp_netif_get_io_driver(netif) : NULL;
        dispose_netif(netif, driver, !station_is_child);
    }
    station_is_child = false;
}

static void destroy_mesh_ap(void) {
    esp_netif_t *netif = mesh_ap_netif;
    mesh_ap_netif = NULL;
    if (netif == NULL) return;
    /* SDK marshals NAPT changes to tcpip_thread, before removing the interface. */
    esp_err_t err = esp_netif_napt_disable(netif);
    if (err != ESP_OK) ESP_LOGE(TAG, "NAPT disable failed: %s", esp_err_to_name(err));
    dispose_netif(netif, esp_netif_get_io_driver(netif), false);
}

static void receive_frame(const mesh_data_t *data, uint32_t generation) {
    lifecycle_enter();
    /* A recv spanning a disconnect/role change cannot target a new interface. */
    if (generation == rx_generation && station_connected) {
        bool root = esp_mesh_is_root();
        if (root && !station_is_child && data->proto == MESH_PROTO_AP && mesh_ap_netif != NULL) {
            esp_netif_receive(mesh_ap_netif, data->data, data->size, NULL);
        } else if (!root && station_is_child && data->proto == MESH_PROTO_STA && station_netif != NULL) {
            esp_netif_receive(station_netif, data->data, data->size, NULL);
        }
    }
    lifecycle_leave();
}

static void receive_task(void *argument) {
    (void)argument;
    static uint8_t buffer[MESH_NETIF_RX_SIZE];
    while (receive_task_running) {
        lifecycle_enter();
        uint32_t generation = rx_generation;
        lifecycle_leave();
        mesh_addr_t from;
        mesh_data_t data = {.data = buffer, .size = sizeof(buffer)};
        int flag = 0;
        esp_err_t err = esp_mesh_recv(&from, &data, portMAX_DELAY, &flag, NULL, 0);
        if (err != ESP_OK) continue;
        diagnostics.rx_success_count++;
        diagnostics.last_rx_success_uptime_s = uptime_seconds();
        receive_frame(&data, generation);
    }
    vTaskDelete(NULL);
}

/* Espressif's start_wifi_link_sta sequence, for recreation after Wi-Fi start. */
static esp_err_t start_default_station_link(esp_netif_t *netif) {
    uint8_t mac[MESH_MAC_LENGTH];
    esp_err_t err = esp_wifi_get_mac(WIFI_IF_STA, mac);
    if (err != ESP_OK) return err;
    err = esp_netif_set_mac(netif, mac);
    if (err != ESP_OK) return err;
    void *driver = esp_netif_get_io_driver(netif);
    err = esp_wifi_register_if_rxcb(driver, esp_netif_receive, netif);
    if (err != ESP_OK) return err;
    esp_netif_action_start(netif, NULL, 0, NULL);
    /* STA is added here, but becomes UP only on action_connected. */
    return ESP_OK;
}

static esp_err_t create_default_station(bool start_link) {
    esp_netif_config_t config = ESP_NETIF_DEFAULT_WIFI_STA();
    esp_netif_t *netif = esp_netif_new(&config);
    if (netif == NULL) return ESP_ERR_NO_MEM;
    /* SDK attach stores driver_handle before post_attach, including on error;
     * clear_default_wifi_driver_and_handlers also clears its global STA slot. */
    esp_err_t err = esp_netif_attach_wifi_station(netif);
    if (err == ESP_OK) err = esp_wifi_set_default_wifi_sta_handlers();
    if (err == ESP_OK && start_link) err = start_default_station_link(netif);
    if (err != ESP_OK) {
        dispose_netif(netif, NULL, true);
        return err;
    }
    station_netif = netif;
    station_is_child = false;
    return ESP_OK;
}

/* Link-connected action starts DHCP only in INIT, preserving repeated events. */
static esp_err_t start_root_external_dhcp(void) {
    esp_netif_action_connected(station_netif, NULL, 0, NULL);
    esp_netif_dhcp_status_t dhcp_status;
    esp_err_t err = esp_netif_dhcpc_get_status(station_netif, &dhcp_status);
    if (err != ESP_OK) return err;
    if (dhcp_status != ESP_NETIF_DHCP_STARTED) return ESP_ERR_INVALID_STATE;
    ESP_LOGI(TAG, "Root external DHCP client state=%s", dhcp_status_name(dhcp_status));
    return ESP_OK;
}

static esp_err_t start_mesh_child_station(void) {
    esp_netif_inherent_config_t base = ESP_NETIF_INHERENT_DEFAULT_WIFI_STA();
    base.if_desc = "omk_mesh_sta";
    esp_netif_config_t config = {.base = &base, .driver = NULL, .stack = ESP_NETIF_NETSTACK_DEFAULT_WIFI_STA};
    esp_netif_t *netif = esp_netif_new(&config);
    if (netif == NULL) return ESP_ERR_NO_MEM;
    mesh_netif_driver_t driver = create_driver(false);
    esp_err_t err = driver == NULL ? ESP_ERR_NO_MEM : esp_netif_attach(netif, driver);
    if (err == ESP_OK) err = esp_wifi_get_mac(WIFI_IF_STA, driver->station_mac);
    if (err == ESP_OK) err = esp_netif_set_mac(netif, driver->station_mac);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Starting child internal STA netif (DHCP client enabled; recreated)");
        esp_netif_action_start(netif, NULL, 0, NULL);
        esp_netif_action_connected(netif, NULL, 0, NULL);
        esp_netif_dhcp_status_t dhcp_status;
        err = esp_netif_dhcpc_get_status(netif, &dhcp_status);
        if (err == ESP_OK && dhcp_status != ESP_NETIF_DHCP_STARTED) err = ESP_ERR_INVALID_STATE;
    }
    if (err != ESP_OK) {
        dispose_netif(netif, driver, false);
        return err;
    }
    station_netif = netif;
    station_is_child = true;
    return ESP_OK;
}

esp_err_t mesh_netifs_init(void) {
    /* Main calls this before event registration; subsequent calls are serialized. */
    if (!lifecycle_mutex) lifecycle_mutex = xSemaphoreCreateMutexStatic(&lifecycle_mutex_storage);
    if (!lifecycle_mutex) return ESP_ERR_NO_MEM;
    lifecycle_enter();
    esp_err_t err = ESP_OK;
    if (!tx_queue) {
        tx_queue = xQueueCreateStatic(MESH_TX_SLOTS, sizeof(unsigned), tx_queue_storage, &tx_queue_control);
        if (!tx_queue) err = ESP_ERR_NO_MEM;
        else if (xTaskCreate(transmit_task, "mesh_netif_tx", 4096, NULL, 5, NULL) != pdPASS) {
            vQueueDelete(tx_queue);
            tx_queue = NULL;
            err = ESP_ERR_NO_MEM;
        }
    }
    if (err == ESP_OK && station_netif == NULL) err = create_default_station(false);
    lifecycle_leave();
    return err;
}

esp_err_t mesh_netif_start_receive_task(void) {
    if (receive_task_running) return ESP_ERR_INVALID_STATE;
    receive_task_running = true;
    if (xTaskCreate(receive_task, "mesh_netif_rx", 3072, NULL, 5, NULL) != pdPASS) {
        receive_task_running = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t mesh_netifs_start(bool is_root) {
    lifecycle_enter();
    invalidate_tx();
    ++rx_generation;
    stopped = false;
    esp_err_t err = ESP_OK;
    if (is_root) {
        if (station_is_child) destroy_station();
        if (station_netif == NULL) err = create_default_station(true);
        if (err == ESP_OK) err = start_root_external_dhcp();
    } else {
        if (!station_is_child) destroy_station();
        destroy_mesh_ap();
        if (station_netif == NULL) err = start_mesh_child_station();
    }
    if (err != ESP_OK) {
        destroy_mesh_ap();
        destroy_station();
    }
    station_connected = err == ESP_OK;
    lifecycle_leave();
    return err;
}

esp_err_t mesh_netifs_stop(void) {
    lifecycle_enter();
    invalidate_tx();
    ++rx_generation;
    esp_err_t err = ESP_OK;
    if (!stopped) {
        ESP_LOGI(TAG, "Stopping current Mesh IP netif");
        destroy_mesh_ap();
        destroy_station();
        err = create_default_station(true);
        stopped = err == ESP_OK;
    }
    lifecycle_leave();
    return err;
}

esp_err_t mesh_netif_start_root_ap(bool is_root, uint32_t dns_addr) {
    lifecycle_enter();
    esp_err_t err = ESP_OK;
    if (!is_root) goto done;
    if (!station_connected || station_is_child || !esp_mesh_is_root()) {
        err = ESP_ERR_INVALID_STATE;
        goto done;
    }
    if (mesh_ap_netif != NULL) goto done;
    esp_netif_inherent_config_t base = ESP_NETIF_INHERENT_DEFAULT_WIFI_AP();
    base.if_desc = "omk_mesh_ap";
    base.ip_info = &mesh_subnet;
    esp_netif_config_t config = {.base = &base, .driver = NULL, .stack = ESP_NETIF_NETSTACK_DEFAULT_WIFI_AP};
    esp_netif_t *netif = esp_netif_new(&config);
    if (netif == NULL) {
        err = ESP_ERR_NO_MEM;
        goto done;
    }
    mesh_netif_driver_t driver = create_driver(true);
    err = driver == NULL ? ESP_ERR_NO_MEM : esp_netif_attach(netif, driver);
    if (err == ESP_OK) err = esp_wifi_get_mac(WIFI_IF_STA, driver->station_mac);
    uint8_t mac[MESH_MAC_LENGTH];
    if (err == ESP_OK) err = esp_wifi_get_mac(WIFI_IF_AP, mac);
    if (err == ESP_OK) err = esp_netif_set_mac(netif, mac);
    esp_netif_dns_info_t dns = {.ip.u_addr.ip4.addr = dns_addr, .ip.type = IPADDR_TYPE_V4};
    dhcps_offer_t offer_dns = OFFER_DNS;
    if (err == ESP_OK) err = esp_netif_dhcps_option(netif, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER,
                                                  &offer_dns, sizeof(offer_dns));
    if (err == ESP_OK) err = esp_netif_set_dns_info(netif, ESP_NETIF_DNS_MAIN, &dns);
    if (err == ESP_OK) {
        err = esp_netif_dhcps_start(netif);
        if (err == ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) err = ESP_OK;
    }
    if (err == ESP_OK) {
        esp_netif_action_start(netif, NULL, 0, NULL);
        esp_netif_dhcp_status_t dhcp_status;
        err = esp_netif_dhcps_get_status(netif, &dhcp_status);
        if (err == ESP_OK && dhcp_status != ESP_NETIF_DHCP_STARTED) err = ESP_ERR_INVALID_STATE;
    }
    if (err == ESP_OK) err = esp_netif_napt_enable(netif);
    if (err != ESP_OK) dispose_netif(netif, driver, false);
    else {
        mesh_ap_netif = netif;
        ESP_LOGI(TAG, "Root internal virtual AP started; DHCP/NAPT enabled for 10.0.0.1");
    }
done:
    lifecycle_leave();
    return err;
}

/* A queued IP event may contain a freed pointer. Never dereference it. Check
 * current ownership/role and IP first, including allocator address reuse. Event
 * consumers and role transitions run on the same default event loop. */
bool mesh_netif_accept_ip_event(const ip_event_got_ip_t *event, bool acquired) {
    if (event == NULL || lifecycle_mutex == NULL) return false;
    lifecycle_enter();
    esp_netif_ip_info_t ip;
    bool valid = station_connected && station_netif == event->esp_netif &&
        station_is_child != esp_mesh_is_root() &&
        esp_netif_get_ip_info(station_netif, &ip) == ESP_OK &&
        (acquired ? (ip.ip.addr != 0 && memcmp(&ip, &event->ip_info, sizeof(ip)) == 0)
                  : ip.ip.addr == 0);
    lifecycle_leave();
    return valid;
}

void mesh_netif_get_diagnostics(mesh_netif_diagnostics_t *result) {
    if (result != NULL) *result = diagnostics;
}
