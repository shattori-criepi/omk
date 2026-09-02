#include "mesh_network.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mesh.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "boot_diagnostics.h"
#include "gateway_credentials.h"
#include "mesh_credentials.h"
#include "mesh_netif.h"
#include "mqtt_registration.h"
#include "node_identity.h"

#define OMK_MESH_STATUS_INTERVAL_MS 30000
#define OMK_MESH_AP_MAX_CONNECTIONS 6
#define OMK_MESH_STATUS_PAYLOAD_SIZE 640

static const char *TAG = "omk-mesh";
static uint8_t parent_bssid[6];
static uint8_t previous_parent_bssid[6];
static uint32_t parent_change_count;
static uint32_t parent_disconnect_count;
static uint32_t last_parent_disconnect_reason;
static uint32_t last_wifi_disconnect_reason;
static uint32_t root_switch_count;
static uint32_t rootless_since_s;
static bool is_rootless;
static bool root_role_known;
static bool previous_is_root;
static bool started;
static esp_ip4_addr_t current_ip;

static uint32_t uptime_seconds(void) {
    return (uint32_t)(esp_timer_get_time() / 1000000);
}

void mesh_network_log_diagnostics(const char *event, uint32_t reason) {
    ESP_LOGI(TAG,
             "%s: uptime=%" PRIu32 "s layer=%d is_root=%s parent_bssid=%02x:%02x:%02x:%02x:%02x:%02x reason=%" PRIu32
             " parent_disconnect_count=%" PRIu32 " mqtt_disconnect_count=%" PRIu32 " free_heap=%u min_free_heap=%u",
             event, uptime_seconds(), esp_mesh_get_layer(), esp_mesh_is_root() ? "true" : "false",
             parent_bssid[0], parent_bssid[1], parent_bssid[2], parent_bssid[3], parent_bssid[4], parent_bssid[5],
             reason, parent_disconnect_count, mqtt_registration_get_disconnect_count(),
             esp_get_free_heap_size(), esp_get_minimum_free_heap_size());
}

/* The first observed role is boot-time election, not a root switch.  Later
 * changes are counted once per local root-role transition, irrespective of
 * how many Mesh events report the same topology transition. */
static void observe_root_role(void) {
    bool is_root = esp_mesh_is_root();
    if (!root_role_known) {
        previous_is_root = is_root;
        root_role_known = true;
        return;
    }
    if (previous_is_root != is_root) {
        root_switch_count++;
        previous_is_root = is_root;
        mesh_network_log_diagnostics(is_root ? "Mesh role switched to root" : "Mesh role switched to child", 0);
    }
}

static void publish_status(void *argument) {
    (void)argument;
    wifi_ap_record_t ap_info = {0};
    int rssi = 0;
    bool rssi_valid = esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK;
    if (rssi_valid) rssi = ap_info.rssi;
    char ip_text[16];
    snprintf(ip_text, sizeof(ip_text), IPSTR, IP2STR(&current_ip));
    uint64_t node_id;
    if (node_identity_get_id(&node_id) != ESP_OK) return;
    char payload[OMK_MESH_STATUS_PAYLOAD_SIZE];
    uint32_t uptime_s = uptime_seconds();
    uint32_t rootless_duration_s = is_rootless ? uptime_s - rootless_since_s : 0;
    int written = snprintf(payload, sizeof(payload),
                           "{\"node_id\":\"%012" PRIx64 "\",\"mesh_layer\":%d,"
                           "\"is_root\":%s,\"parent_bssid\":\"%02x:%02x:%02x:%02x:%02x:%02x\","
                           "\"rssi_dbm\":%d,\"rssi_valid\":%s,\"ip\":\"%s\","
                           "\"parent_change_count\":%" PRIu32 ","
                           "\"parent_disconnect_count\":%" PRIu32 ","
                           "\"is_rootless\":%s,\"rootless_duration_s\":%" PRIu32 ","
                           "\"last_parent_disconnect_reason\":%" PRIu32 ","
                           "\"last_wifi_disconnect_reason\":%" PRIu32 ","
                           "\"root_switch_count\":%" PRIu32 ","
                           "\"mqtt_disconnect_count\":%" PRIu32 ","
                           "\"uptime_s\":%" PRIu32 ",\"free_heap_bytes\":%" PRIu32 ","
                           "\"minimum_free_heap_bytes\":%" PRIu32 ","
                           "\"reset_reason\":\"%s\",\"reset_reason_code\":%" PRIu32 ","
                           "\"boot_count\":%" PRIu32 "}",
                           node_id, esp_mesh_get_layer(), esp_mesh_is_root() ? "true" : "false",
                           parent_bssid[0], parent_bssid[1], parent_bssid[2], parent_bssid[3],
                           parent_bssid[4], parent_bssid[5], rssi, rssi_valid ? "true" : "false", ip_text,
                           parent_change_count, parent_disconnect_count,
                           is_rootless ? "true" : "false", rootless_duration_s,
                           last_parent_disconnect_reason, last_wifi_disconnect_reason,
                           root_switch_count,
                           mqtt_registration_get_disconnect_count(),
                           uptime_s, esp_get_free_heap_size(), esp_get_minimum_free_heap_size(),
                           boot_diagnostics_reset_reason(), boot_diagnostics_reset_reason_code(),
                           boot_diagnostics_boot_count());
    if (written > 0 && written < (int)sizeof(payload)) {
        (void)mqtt_registration_publish_mesh_status(payload);
    }
}

static void ip_event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)argument;
    (void)base;
    (void)id;
    ip_event_got_ip_t *event = data;
    if (event == NULL) return;
    current_ip.addr = event->ip_info.ip.addr;
    ESP_LOGI(TAG, "%s STA IP acquired: ip=" IPSTR " gw=" IPSTR " mask=" IPSTR,
             esp_mesh_is_root() ? "Root external" : "Child internal",
             IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.gw),
             IP2STR(&event->ip_info.netmask));
    mesh_network_log_diagnostics("Mesh IP ready", 0);
    if (!esp_mesh_is_root()) return;
    esp_netif_dns_info_t dns;
    if (esp_netif_get_dns_info(event->esp_netif, ESP_NETIF_DNS_MAIN, &dns) == ESP_OK) {
        esp_err_t err = mesh_netif_start_root_ap(true, dns.ip.u_addr.ip4.addr);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Could not start root internal virtual AP/NAPT: %s", esp_err_to_name(err));
        }
    } else {
        ESP_LOGW(TAG, "Could not read root external DNS; internal virtual AP/NAPT not started");
    }
}

static void mesh_event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)argument;
    (void)base;
    switch (id) {
    case MESH_EVENT_PARENT_CONNECTED: {
        const mesh_event_connected_t *event = data;
        if (event == NULL) break;
        if (memcmp(previous_parent_bssid, event->connected.bssid, sizeof(parent_bssid)) != 0) {
            static const uint8_t zero[6];
            if (memcmp(previous_parent_bssid, zero, sizeof(zero)) != 0) parent_change_count++;
            memcpy(previous_parent_bssid, event->connected.bssid, sizeof(parent_bssid));
        }
        memcpy(parent_bssid, event->connected.bssid, sizeof(parent_bssid));
        is_rootless = false;
        rootless_since_s = 0;
        mesh_network_log_diagnostics("Mesh parent connected", 0);
        esp_err_t err = mesh_netifs_start(esp_mesh_is_root());
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Could not start Mesh IP netif for parent connection: %s", esp_err_to_name(err));
        }
        observe_root_role();
        break;
    }
    case MESH_EVENT_PARENT_DISCONNECTED: {
        const mesh_event_disconnected_t *event = data;
        parent_disconnect_count++;
        if (event != NULL) {
            last_parent_disconnect_reason = event->reason;
            /* ESP-IDF defines mesh_event_disconnected_t as the Wi-Fi STA
             * disconnect payload.  Use Mesh's event path so its internally
             * owned STA event handling remains untouched. */
            last_wifi_disconnect_reason = event->reason;
        }
        mesh_network_log_diagnostics("Mesh parent disconnected / Wi-Fi STA disconnected",
                                     last_parent_disconnect_reason);
        current_ip.addr = 0;
        mesh_network_log_diagnostics("Mesh IP lost", last_parent_disconnect_reason);
        (void)mesh_netifs_stop();
        observe_root_role();
        break;
    }
    case MESH_EVENT_NETWORK_STATE: {
        const mesh_event_network_state_t *event = data;
        if (event == NULL) break;
        if (event->is_rootless && !is_rootless) rootless_since_s = uptime_seconds();
        if (!event->is_rootless) rootless_since_s = 0;
        is_rootless = event->is_rootless;
        break;
    }
    case MESH_EVENT_ROOT_SWITCH_ACK:
        is_rootless = false;
        rootless_since_s = 0;
        observe_root_role();
        break;
    default:
        break;
    }
}

esp_err_t mesh_network_start_prepared(void) {
    if (started) return ESP_ERR_INVALID_STATE;
    if (!mesh_credentials_test_vector_matches()) return ESP_FAIL;
    omk_gateway_credentials_t gateway = {0};
    esp_err_t err = gateway_credentials_load(&gateway);
    if (err != ESP_OK) return err;
    uint8_t mesh_id[6];
    char mesh_password[OMK_MESH_AP_PASSWORD_LENGTH + 1];
    err = mesh_credentials_derive(gateway.ssid, gateway.ssid_length, gateway.psk, gateway.psk_length,
                                  mesh_id, mesh_password);
    if (err != ESP_OK) return err;
    err = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, ip_event_handler, NULL);
    if (err != ESP_OK) return err;
    err = esp_wifi_set_ps(WIFI_PS_NONE);
    if (err != ESP_OK) return err;
    err = esp_wifi_start();
    if (err != ESP_OK) return err;
    err = esp_mesh_init();
    if (err != ESP_OK) return err;
    err = esp_event_handler_register(MESH_EVENT, ESP_EVENT_ANY_ID, mesh_event_handler, NULL);
    if (err != ESP_OK) return err;
    err = esp_mesh_set_max_layer(CONFIG_MESH_MAX_LAYER);
    if (err != ESP_OK) return err;
    (void)esp_mesh_set_vote_percentage(1);
    (void)esp_mesh_set_ap_assoc_expire(30);
    (void)esp_mesh_send_block_time(30000);
    mesh_cfg_t config = MESH_INIT_CONFIG_DEFAULT();
    memcpy(config.mesh_id.addr, mesh_id, sizeof(mesh_id));
    config.channel = 0;
    config.router.ssid_len = gateway.ssid_length;
    memcpy(config.router.ssid, gateway.ssid, gateway.ssid_length);
    memcpy(config.router.password, gateway.psk, gateway.psk_length);
    err = esp_mesh_set_ap_authmode(WIFI_AUTH_WPA2_PSK);
    if (err != ESP_OK) return err;
    config.mesh_ap.max_connection = OMK_MESH_AP_MAX_CONNECTIONS;
    config.mesh_ap.nonmesh_max_connection = 0;
    memcpy(config.mesh_ap.password, mesh_password, OMK_MESH_AP_PASSWORD_LENGTH);
    err = esp_mesh_set_config(&config);
    if (err != ESP_OK) return err;
    err = esp_mesh_start();
    if (err != ESP_OK) return err;
    err = mesh_netif_start_receive_task();
    if (err != ESP_OK) return err;
    esp_timer_handle_t status_timer;
    const esp_timer_create_args_t timer_config = {.callback = publish_status, .name = "mesh_status"};
    err = esp_timer_create(&timer_config, &status_timer);
    if (err != ESP_OK) return err;
    err = esp_timer_start_periodic(status_timer, OMK_MESH_STATUS_INTERVAL_MS * 1000ULL);
    if (err != ESP_OK) return err;
    started = true;
    ESP_LOGI(TAG, "ESP-WIFI-MESH started");
    return ESP_OK;
}
