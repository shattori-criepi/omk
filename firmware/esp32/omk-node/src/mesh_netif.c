#include "mesh_netif.h"

#include <stdlib.h>
#include <string.h>

#include "dhcpserver/dhcpserver.h"
#include "esp_log.h"
#include "esp_mesh.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_wifi_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/lwip_napt.h"

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
static mesh_addr_t routing_table[CONFIG_MESH_ROUTE_TABLE_SIZE];

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

static esp_err_t transmit_root_ap(void *handle, void *buffer, size_t length) {
    static const uint8_t broadcast[MESH_MAC_LENGTH] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    mesh_netif_driver_t driver = handle;
    mesh_addr_t destination;
    mesh_data_t data = {.data = buffer, .size = length, .proto = MESH_PROTO_STA, .tos = MESH_TOS_P2P};
    memcpy(destination.addr, buffer, MESH_MAC_LENGTH);
    if (memcmp(destination.addr, broadcast, MESH_MAC_LENGTH) != 0) {
        return esp_mesh_send(&destination, &data, MESH_DATA_P2P, NULL, 0);
    }
    int count = 0;
    esp_err_t result = esp_mesh_get_routing_table(routing_table, sizeof(routing_table), &count);
    if (result != ESP_OK) return result;
    for (int index = 0; index < count; ++index) {
        if (memcmp(routing_table[index].addr, driver->station_mac, MESH_MAC_LENGTH) != 0) {
            (void)esp_mesh_send(&routing_table[index], &data, MESH_DATA_P2P, NULL, 0);
        }
    }
    return ESP_OK;
}

static esp_err_t transmit_root_ap_wrap(void *handle, void *buffer, size_t length, void *netstack_buffer) {
    (void)netstack_buffer;
    return transmit_root_ap(handle, buffer, length);
}

static esp_err_t transmit_child_sta(void *handle, void *buffer, size_t length) {
    (void)handle;
    mesh_data_t data = {.data = buffer, .size = length, .proto = MESH_PROTO_AP, .tos = MESH_TOS_P2P};
    return esp_mesh_send(NULL, &data, MESH_DATA_TODS, NULL, 0);
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
    if (driver == NULL) return NULL;
    driver->base.post_attach = root_ap ? attach_root_ap_driver : attach_child_sta_driver;
    (void)esp_wifi_get_mac(WIFI_IF_STA, driver->station_mac);
    return driver;
}

static void destroy_mesh_driver(esp_netif_t *netif) {
    mesh_netif_driver_t driver = esp_netif_get_io_driver(netif);
    free(driver);
}

static void receive_task(void *argument) {
    (void)argument;
    static uint8_t buffer[MESH_NETIF_RX_SIZE];
    while (receive_task_running) {
        mesh_addr_t from;
        mesh_data_t data = {.data = buffer, .size = sizeof(buffer)};
        int flag = 0;
        esp_err_t err = esp_mesh_recv(&from, &data, portMAX_DELAY, &flag, NULL, 0);
        if (err != ESP_OK) continue;
        if (esp_mesh_is_root() && data.proto == MESH_PROTO_AP && mesh_ap_netif != NULL) {
            esp_netif_receive(mesh_ap_netif, data.data, data.size, NULL);
        } else if (!esp_mesh_is_root() && data.proto == MESH_PROTO_STA && station_netif != NULL) {
            esp_netif_receive(station_netif, data.data, data.size, NULL);
        }
    }
    vTaskDelete(NULL);
}

static esp_err_t create_default_station(void) {
    esp_netif_config_t config = ESP_NETIF_DEFAULT_WIFI_STA();
    station_netif = esp_netif_new(&config);
    if (station_netif == NULL) return ESP_ERR_NO_MEM;
    esp_err_t err = esp_netif_attach_wifi_station(station_netif);
    if (err == ESP_OK) err = esp_wifi_set_default_wifi_sta_handlers();
    return err;
}

/* This is Espressif's start_wifi_link_sta() sequence. It is needed only when
 * recreating the default STA after esp_wifi_start() has already occurred. */
static esp_err_t start_default_station_link(void) {
    uint8_t mac[MESH_MAC_LENGTH];
    esp_err_t err = esp_wifi_get_mac(WIFI_IF_STA, mac);
    if (err != ESP_OK) return err;
    void *driver = esp_netif_get_io_driver(station_netif);
    err = esp_wifi_register_if_rxcb(driver, esp_netif_receive, station_netif);
    if (err != ESP_OK) return err;
    err = esp_netif_set_mac(station_netif, mac);
    if (err != ESP_OK) return err;
    esp_netif_action_start(station_netif, NULL, 0, NULL);
    return ESP_OK;
}

static void destroy_mesh_ap(void) {
    if (mesh_ap_netif == NULL) return;
    (void)esp_netif_dhcps_stop(mesh_ap_netif);
    (void)esp_netif_action_disconnected(mesh_ap_netif, NULL, 0, NULL);
    destroy_mesh_driver(mesh_ap_netif);
    esp_netif_destroy(mesh_ap_netif);
    mesh_ap_netif = NULL;
}

static esp_err_t start_mesh_child_station(void) {
    esp_netif_inherent_config_t base = ESP_NETIF_INHERENT_DEFAULT_WIFI_STA();
    base.if_desc = "omk_mesh_sta";
    esp_netif_config_t config = {.base = &base, .driver = NULL, .stack = ESP_NETIF_NETSTACK_DEFAULT_WIFI_STA};
    station_netif = esp_netif_new(&config);
    if (station_netif == NULL) return ESP_ERR_NO_MEM;
    mesh_netif_driver_t driver = create_driver(false);
    if (driver == NULL) return ESP_ERR_NO_MEM;
    esp_err_t err = esp_netif_attach(station_netif, driver);
    if (err != ESP_OK) return err;
    uint8_t mac[MESH_MAC_LENGTH];
    (void)esp_wifi_get_mac(WIFI_IF_STA, mac);
    (void)esp_netif_set_mac(station_netif, mac);
    ESP_LOGI(TAG, "Child internal STA netif starting (DHCP client enabled)");
    (void)esp_netif_action_start(station_netif, NULL, 0, NULL);
    esp_netif_action_connected(station_netif, NULL, 0, NULL);
    esp_netif_dhcp_status_t dhcp_status;
    err = esp_netif_dhcpc_get_status(station_netif, &dhcp_status);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Child internal DHCP client state=%s", dhcp_status_name(dhcp_status));
    } else {
        ESP_LOGW(TAG, "Could not read child internal DHCP client state: %s", esp_err_to_name(err));
    }
    return ESP_OK;
}

esp_err_t mesh_netifs_init(void) {
    return create_default_station();
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
    if (is_root) {
        ESP_LOGI(TAG, "Root external STA netif starting after Mesh parent connection");
        if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), "omk_mesh_sta") == 0) {
            (void)esp_netif_action_disconnected(station_netif, NULL, 0, NULL);
            destroy_mesh_driver(station_netif);
            esp_netif_destroy(station_netif);
            station_netif = NULL;
            esp_err_t err = create_default_station();
            if (err != ESP_OK) return err;
        }
        if (station_netif == NULL) {
            esp_err_t err = create_default_station();
            if (err != ESP_OK) return err;
        }
        /* ESP-WIFI-MESH owns association.  Restart DHCP only after the
         * root's parent-connected event, as in Espressif's Mesh examples. */
        esp_err_t err = esp_netif_dhcpc_stop(station_netif);
        if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) return err;
        return esp_netif_dhcpc_start(station_netif);
    }
    ESP_LOGI(TAG, "Child internal netif starting after Mesh parent connection");
    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), "omk_mesh_sta") == 0) return ESP_OK;
    if (station_netif != NULL) {
        (void)esp_netif_action_disconnected(station_netif, NULL, 0, NULL);
        (void)esp_wifi_clear_default_wifi_driver_and_handlers(station_netif);
        esp_netif_destroy(station_netif);
        station_netif = NULL;
    }
    destroy_mesh_ap();
    return start_mesh_child_station();
}

esp_err_t mesh_netifs_stop(void) {
    if (station_netif != NULL) {
        if (strcmp(esp_netif_get_desc(station_netif), "omk_mesh_sta") == 0) {
            (void)esp_netif_action_disconnected(station_netif, NULL, 0, NULL);
            destroy_mesh_driver(station_netif);
        } else {
            (void)esp_wifi_clear_default_wifi_driver_and_handlers(station_netif);
        }
        esp_netif_destroy(station_netif);
        station_netif = NULL;
    }
    destroy_mesh_ap();
    esp_err_t err = create_default_station();
    if (err != ESP_OK) return err;
    return start_default_station_link();
}

esp_err_t mesh_netif_start_root_ap(bool is_root, uint32_t dns_addr) {
    if (!is_root || mesh_ap_netif != NULL) return ESP_OK;
    esp_netif_inherent_config_t base = ESP_NETIF_INHERENT_DEFAULT_WIFI_AP();
    base.if_desc = "omk_mesh_ap";
    base.ip_info = &mesh_subnet;
    esp_netif_config_t config = {.base = &base, .driver = NULL, .stack = ESP_NETIF_NETSTACK_DEFAULT_WIFI_AP};
    mesh_ap_netif = esp_netif_new(&config);
    if (mesh_ap_netif == NULL) return ESP_ERR_NO_MEM;
    mesh_netif_driver_t driver = create_driver(true);
    if (driver == NULL) return ESP_ERR_NO_MEM;
    esp_err_t err = esp_netif_attach(mesh_ap_netif, driver);
    if (err != ESP_OK) return err;
    esp_netif_dns_info_t dns = {.ip.u_addr.ip4.addr = dns_addr, .ip.type = IPADDR_TYPE_V4};
    dhcps_offer_t offer_dns = OFFER_DNS;
    (void)esp_netif_dhcps_option(mesh_ap_netif, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER,
                                 &offer_dns, sizeof(offer_dns));
    (void)esp_netif_set_dns_info(mesh_ap_netif, ESP_NETIF_DNS_MAIN, &dns);
    err = esp_netif_dhcps_start(mesh_ap_netif);
    if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) {
        ESP_LOGW(TAG, "Root internal DHCP server start failed: %s", esp_err_to_name(err));
    }
    uint8_t mac[MESH_MAC_LENGTH];
    (void)esp_wifi_get_mac(WIFI_IF_AP, mac);
    (void)esp_netif_set_mac(mesh_ap_netif, mac);
    (void)esp_netif_action_start(mesh_ap_netif, NULL, 0, NULL);
    esp_netif_dhcp_status_t dhcp_status;
    err = esp_netif_dhcps_get_status(mesh_ap_netif, &dhcp_status);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Root internal virtual AP started; DHCP server state=%s",
                 dhcp_status_name(dhcp_status));
    } else {
        ESP_LOGW(TAG, "Could not read root internal DHCP server state: %s", esp_err_to_name(err));
    }
    ip_napt_enable(mesh_subnet.ip.addr, 1);
    ESP_LOGI(TAG, "Root internal NAPT enabled for 10.0.0.1");
    return ESP_OK;
}
