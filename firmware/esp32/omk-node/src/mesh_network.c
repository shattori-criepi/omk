#include "mesh_network.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mesh.h"
/* Parent RSSI threshold controls are currently exposed by ESP-IDF as an internal Mesh API. */
#include "esp_mesh_internal.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "boot_diagnostics.h"
#include "gateway_credentials.h"
#include "mesh_credentials.h"
#include "mesh_recovery.h"
#include "mesh_recovery_store.h"
#include "mesh_netif.h"
#include "mesh_root_recovery.h"
#include "mqtt_registration.h"
#include "node_identity.h"

#define OMK_MESH_STATUS_INTERVAL_MS 30000
#define OMK_MESH_AP_MAX_CONNECTIONS 6
#define OMK_MESH_STATUS_PAYLOAD_SIZE 1152

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
static bool parent_connected;
static esp_ip4_addr_t current_ip;
/* Owned by the default event loop, including periodic recovery/status. */
ESP_EVENT_DEFINE_BASE(OMK_MESH_CONTROL_EVENT);
static omk_mesh_recovery_t recovery;
static omk_mesh_recovery_record_t recovery_record;
static omk_mesh_root_recovery_t root_recovery;

static esp_err_t configure_parent_rssi_thresholds(void) {
    const mesh_rssi_threshold_t configured = {
        .high = CONFIG_MESH_PARENT_RSSI_HIGH,
        .medium = CONFIG_MESH_PARENT_RSSI_MEDIUM,
        .low = CONFIG_MESH_PARENT_RSSI_LOW,
    };
    esp_err_t err = esp_mesh_set_rssi_threshold(&configured);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not set Mesh RSSI thresholds: %s", esp_err_to_name(err));
        return err;
    }

    mesh_rssi_threshold_t applied = {0};
    err = esp_mesh_get_rssi_threshold(&applied);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not read back Mesh RSSI thresholds: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "Mesh RSSI thresholds: high=%d medium=%d low=%d dBm",
             applied.high, applied.medium, applied.low);
    return ESP_OK;
}

static void log_parent_switch_parameters(void) {
    mesh_switch_parent_t p = {0};
    esp_err_t err = esp_mesh_get_switch_parent_paras(&p);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Mesh parent switching read-back failed: %s", esp_err_to_name(err));
        return;
    }
    /* IDF ships libmesh.a, not its implementation. Do not invent defaults or
     * interpret switch_rssi as an improvement delta; retain runtime values. */
    ESP_LOGI(TAG, "Mesh parent switching (IDF runtime): self_organized=%d duration_ms=%d"
             " cnx_rssi=%d select_rssi=%d switch_rssi=%d backoff_rssi=%d",
             esp_mesh_get_self_organized(), p.duration_ms, p.cnx_rssi,
             p.select_rssi, p.switch_rssi, p.backoff_rssi);
}

static esp_err_t disable_root_conflicts(void) {
    esp_err_t err = esp_mesh_allow_root_conflicts(false);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not disable Mesh root conflicts: %s", esp_err_to_name(err));
        return err;
    }
    if (esp_mesh_is_root_conflicts_allowed()) {
        ESP_LOGE(TAG, "Mesh root conflicts remain enabled after configuration");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Mesh root conflicts: disabled");
    return ESP_OK;
}

static uint32_t uptime_seconds(void) {
    return (uint32_t)(esp_timer_get_time() / 1000000);
}

static uint32_t uptime_milliseconds(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/* Keep both recovery levels in the periodic control path, rather than in a disconnect event:
 * a request observed while disconnected remains pending until the root has a
 * parent again. */
static void try_root_recovery(void) {
    uint32_t now_ms = uptime_milliseconds();
    omk_mesh_root_recovery_action_t action = mesh_root_recovery_should_execute(
        &root_recovery, now_ms, esp_mesh_is_root(), parent_connected, is_rootless);
    if (action == OMK_MESH_ROOT_RECOVERY_NONE) return;
    if (action == OMK_MESH_ROOT_RECOVERY_PARENT_RESELECTION) {
        ESP_LOGI(TAG, "root recovery requested: sustained_weak_root (level=2)");
        /* ESP-IDF 6.0.1: select_parent=true makes a root disconnect from its
         * router and children and search for a preferred parent as a non-root.
         * Parent choice remains entirely with self-organized ESP-WIFI-MESH. */
        esp_err_t err = esp_mesh_set_self_organized(true, true);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "root parent reselection failed: sustained_weak_root (%s)", esp_err_to_name(err));
        } else {
            mesh_root_recovery_mark_executed(&root_recovery, now_ms, action);
            if (recovery.parent_reselection_count < UINT32_MAX) recovery.parent_reselection_count++;
            ESP_LOGI(TAG, "root parent reselection executed: sustained_weak_root (level=2)");
        }
        /* On failure retry next status cycle, without a vote in this cycle or
         * a successful-recovery cooldown/grace period. */
        return;
    }
    const char *reason = mesh_root_recovery_reason_name(root_recovery.reason);
    ESP_LOGI(TAG, "root reelection requested: %s (level=1)", reason);
    esp_err_t err = esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "root reelection deferred: %s (%s)", reason, esp_err_to_name(err));
        return;
    }
    mesh_root_recovery_mark_executed(&root_recovery, now_ms, action);
    ESP_LOGI(TAG, "root reelection executed: %s (level=1)", reason);
}

void mesh_network_log_diagnostics(const char *event, uint32_t reason) {
    wifi_ap_record_t ap_info = {0};
    bool rssi_valid = esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK;
    char ip_text[16];
    snprintf(ip_text, sizeof(ip_text), IPSTR, IP2STR(&current_ip));
    ESP_LOGI(TAG,
             "%s: uptime=%" PRIu32 "s layer=%d is_root=%s parent_bssid=%02x:%02x:%02x:%02x:%02x:%02x"
             " rssi_dbm=%d rssi_valid=%s ip=%s rootless=%s reason=%" PRIu32
             " parent_disconnect_count=%" PRIu32 " mqtt_disconnect_count=%" PRIu32
             " mqtt_connected=%s mqtt_disconnected_duration_s=%" PRIu32
             " free_heap=%u min_free_heap=%u",
             event, uptime_seconds(), esp_mesh_get_layer(), esp_mesh_is_root() ? "true" : "false",
             parent_bssid[0], parent_bssid[1], parent_bssid[2], parent_bssid[3], parent_bssid[4], parent_bssid[5],
             rssi_valid ? ap_info.rssi : 0, rssi_valid ? "true" : "false", ip_text,
             is_rootless ? "true" : "false",
             reason, parent_disconnect_count, mqtt_registration_get_disconnect_count(),
             mqtt_registration_is_connected() ? "true" : "false",
             mqtt_registration_get_disconnected_duration_s(),
             esp_get_free_heap_size(), esp_get_minimum_free_heap_size());
}

static omk_mesh_recovery_input_t recovery_input(void) {
    return (omk_mesh_recovery_input_t){
        .started = started, .is_root = esp_mesh_is_root(),
        .parent_connected = parent_connected, .rootless = is_rootless,
        .has_ip = current_ip.addr != 0,
        .mqtt_started = mqtt_registration_is_started(),
        .mqtt_connected = mqtt_registration_is_connected(),
        .mqtt_disconnect_count = mqtt_registration_get_disconnect_count(),
    };
}

static void observe_recovery(void) {
    omk_mesh_recovery_input_t in = recovery_input();
    mesh_recovery_observe(&recovery, uptime_milliseconds(), &in);
}

static void run_recovery_action(omk_mesh_recovery_action_t action, uint32_t now_ms) {
    esp_err_t err = ESP_OK;
    switch (action) {
    case OMK_RECOVERY_CHILD_RESELECT:
        /* Revalidate role immediately before a potentially disruptive API. */
        if (esp_mesh_is_root() || (parent_connected && !is_rootless)) return;
        err = esp_mesh_set_self_organized(true, true);
        break;
    case OMK_RECOVERY_ROUTER_RECONNECT:
        if (!esp_mesh_is_root() || parent_connected) return;
        err = esp_mesh_set_self_organized(true, false);
        if (err == ESP_OK) {
            if (!esp_mesh_is_root() || parent_connected) return;
            err = esp_mesh_connect();
        }
        break;
    case OMK_RECOVERY_WEAK_ROOT:
        recovery.reason = OMK_RECOVERY_SEVERE_ROOT;
        try_root_recovery();
        return;
    case OMK_RECOVERY_ROOT_VOTE:
        recovery.reason = root_recovery.reason == OMK_MESH_ROOT_REELECTION_TOPOLOGY_CHANGE
                              ? OMK_RECOVERY_TOPOLOGY : OMK_RECOVERY_ROOT_LINK;
        try_root_recovery();
        return;
    case OMK_RECOVERY_RESTART:
        err = mesh_recovery_store_save(&recovery_record, true, recovery.reason);
        mesh_recovery_complete(&recovery, now_ms, action, err == ESP_OK);
        if (err == ESP_OK) {
            const char *reason = recovery.reason == OMK_RECOVERY_MQTT_ONLY
                                     ? "mesh_mqtt_liveness_timeout" : "mesh_parent_loss_timeout";
            mesh_network_log_diagnostics(reason, 0);
            boot_diagnostics_record_restart_reason(reason);
            esp_restart();
        } else {
            ESP_LOGE(TAG, "Recovery restart blocked: budget persistence failed (%s)", esp_err_to_name(err));
        }
        return;
    case OMK_RECOVERY_REARM:
        err = mesh_recovery_store_save(&recovery_record, false, recovery.reason);
        break;
    default:
        return;
    }
    mesh_recovery_complete(&recovery, now_ms, action, err == ESP_OK);
    ESP_LOGI(TAG, "Mesh recovery action=%d reason=%s request=%s stage=%s budget_spent=%d",
             action, mesh_recovery_reason_name(recovery.reason), esp_err_to_name(err),
             mesh_recovery_stage_name(recovery.stage), recovery.restart_spent);
}

static void try_recovery(void) {
    uint32_t now_ms = uptime_milliseconds();
    omk_mesh_recovery_input_t in = recovery_input();
    omk_mesh_recovery_action_t action = mesh_recovery_choose(&recovery, now_ms, &in, &root_recovery);
    run_recovery_action(action, now_ms);
}

static void publish_recovery_status(uint64_t node_id) {
    /* A companion status avoids overflowing or removing existing 1152-byte
     * node/status fields. Counters survive reconnection; the restart record
     * also survives reboot. No backlog is accumulated while disconnected. */
    /* Default event-loop stack is small. This buffer has one serialized owner. */
    static char payload[768];
    int n = snprintf(payload, sizeof(payload),
        "{\"node_id\":\"%012" PRIx64 "\",\"uptime_s\":%" PRIu32
        ",\"recovery_stage\":\"%s\",\"last_recovery_reason\":\"%s\""
        ",\"no_parent_found_count\":%" PRIu32 ",\"last_scan_times\":%d"
        ",\"stop_reconnection_count\":%" PRIu32 ",\"parent_reselection_count\":%" PRIu32
        ",\"parent_loss_duration_s\":%" PRIu32 ",\"restart_budget_spent\":%s"
        ",\"recovery_restart_count\":%" PRIu32 ",\"last_restart_reason\":\"%s\"}",
        node_id, uptime_seconds(), mesh_recovery_stage_name(recovery.stage),
        mesh_recovery_reason_name(recovery.reason), recovery.no_parent_found_count,
        recovery.last_scan_times, recovery.stop_reconnection_count, recovery.parent_reselection_count,
        mesh_recovery_loss_duration_s(&recovery, uptime_milliseconds()),
        recovery.restart_spent ? "true" : "false", recovery_record.restart_count,
        mesh_recovery_reason_name((omk_mesh_recovery_reason_t)recovery_record.reason));
    if (n > 0 && n < (int)sizeof(payload))
        (void)mqtt_registration_publish_mesh_recovery_status(payload);
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
    mesh_root_recovery_observe_link(&root_recovery, uptime_milliseconds(), esp_mesh_is_root(),
                                    rssi_valid, rssi, parent_disconnect_count,
                                    mqtt_registration_get_disconnect_count());
    try_recovery();
    char ip_text[16];
    snprintf(ip_text, sizeof(ip_text), IPSTR, IP2STR(&current_ip));
    uint64_t node_id;
    if (node_identity_get_id(&node_id) != ESP_OK) return;
    static char payload[OMK_MESH_STATUS_PAYLOAD_SIZE];
    uint32_t uptime_s = uptime_seconds();
    uint32_t rootless_duration_s = is_rootless ? uptime_s - rootless_since_s : 0;
    mesh_netif_diagnostics_t netif_diagnostics;
    mesh_netif_get_diagnostics(&netif_diagnostics);
    uint32_t mqtt_disconnected_duration_s = mqtt_registration_get_disconnected_duration_s();
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
                           "\"mqtt_connected\":%s,\"mqtt_disconnected_duration_s\":%" PRIu32 ","
                           "\"mqtt_last_connected_uptime_s\":%" PRIu32 ","
                           "\"mesh_rx_success_count\":%" PRIu32 ","
                           "\"mesh_tx_success_count\":%" PRIu32 ","
                           "\"mesh_tx_failure_count\":%" PRIu32 ","
                           "\"mesh_last_rx_success_uptime_s\":%" PRIu32 ","
                           "\"mesh_last_tx_success_uptime_s\":%" PRIu32 ","
                           "\"uptime_s\":%" PRIu32 ",\"free_heap_bytes\":%" PRIu32 ","
                           "\"minimum_free_heap_bytes\":%" PRIu32 ","
                           "\"reset_reason\":\"%s\",\"reset_reason_code\":%" PRIu32 ","
                           "\"boot_count\":%" PRIu32 ","
                           "\"last_omk_restart_reason\":\"%s\","
                           "\"mesh_mqtt_liveness_restart_count\":%" PRIu32 "}",
                           node_id, esp_mesh_get_layer(), esp_mesh_is_root() ? "true" : "false",
                           parent_bssid[0], parent_bssid[1], parent_bssid[2], parent_bssid[3],
                           parent_bssid[4], parent_bssid[5], rssi, rssi_valid ? "true" : "false", ip_text,
                           parent_change_count, parent_disconnect_count,
                           is_rootless ? "true" : "false", rootless_duration_s,
                           last_parent_disconnect_reason, last_wifi_disconnect_reason,
                           root_switch_count,
                           mqtt_registration_get_disconnect_count(),
                           mqtt_registration_is_connected() ? "true" : "false",
                           mqtt_disconnected_duration_s,
                           mqtt_registration_get_last_connected_uptime_s(),
                           netif_diagnostics.rx_success_count, netif_diagnostics.tx_success_count,
                           netif_diagnostics.tx_failure_count,
                           netif_diagnostics.last_rx_success_uptime_s,
                           netif_diagnostics.last_tx_success_uptime_s,
                           uptime_s, esp_get_free_heap_size(), esp_get_minimum_free_heap_size(),
                           boot_diagnostics_reset_reason(), boot_diagnostics_reset_reason_code(),
                           boot_diagnostics_boot_count(),
                           boot_diagnostics_last_omk_restart_reason(),
                           boot_diagnostics_mesh_mqtt_liveness_restart_count());
    if (written > 0 && written < (int)sizeof(payload)) {
        (void)mqtt_registration_publish_mesh_status(payload);
    }
    publish_recovery_status(node_id);
}

static void ip_event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)argument;
    (void)base;
    if (id == IP_EVENT_STA_LOST_IP) {
        current_ip.addr = 0;
        observe_recovery();
        return;
    }
    if (id != IP_EVENT_STA_GOT_IP) return;
    ip_event_got_ip_t *event = data;
    if (event == NULL) return;
    current_ip.addr = event->ip_info.ip.addr;
    observe_recovery();
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
        parent_connected = true;
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
        parent_connected = false;
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
        /* Rootless describes the tree, not the local STA association. */
        break;
    }
    case MESH_EVENT_NO_PARENT_FOUND: {
        const mesh_event_no_parent_found_t *event = data;
        observe_recovery();
        mesh_recovery_note_no_parent(&recovery, event != NULL ? event->scan_times : -1);
        ESP_LOGW(TAG, "Mesh no parent found: count=%" PRIu32 " scan_times=%d stage=%s",
                 recovery.no_parent_found_count, recovery.last_scan_times,
                 mesh_recovery_stage_name(recovery.stage));
        break;
    }
    case MESH_EVENT_STOP_RECONNECTION:
        observe_recovery();
        mesh_recovery_note_stopped(&recovery);
        ESP_LOGW(TAG, "Mesh stopped reconnection: count=%" PRIu32 " stage=%s",
                 recovery.stop_reconnection_count, mesh_recovery_stage_name(recovery.stage));
        break;
    case MESH_EVENT_ROOT_SWITCH_ACK:
        is_rootless = false;
        rootless_since_s = 0;
        observe_root_role();
        break;
    case MESH_EVENT_ROUTING_TABLE_ADD:
        /* This is the Mesh-stack event for a newly joined descendant.  On the
         * current root it covers a node joining or returning after maintenance;
         * wait for topology stabilization before asking Mesh to vote again. */
        if (esp_mesh_is_root()) {
            uint32_t now_ms = uptime_milliseconds();
            if (root_recovery.has_executed &&
                (int32_t)(now_ms - root_recovery.cooldown_until_ms) < 0) {
                ESP_LOGI(TAG, "root reelection deferred: cooldown");
            } else {
                mesh_root_recovery_note_topology_change(&root_recovery, now_ms, true);
                ESP_LOGI(TAG, "root reelection requested: topology_change (stabilizing)");
            }
        }
        break;
    default:
        break;
    }
    observe_recovery();
}

/* Timer callbacks never race Mesh/IP handlers or block the ESP timer task. */
static void status_timer_callback(void *argument) {
    (void)argument;
    esp_err_t err = esp_event_post(OMK_MESH_CONTROL_EVENT, 0, NULL, 0, 0);
    if (err != ESP_OK) ESP_LOGW(TAG, "Mesh status cycle deferred: %s", esp_err_to_name(err));
}

static void status_event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)base;
    (void)id;
    (void)data;
    publish_status(argument);
}

esp_err_t mesh_network_start_prepared(void) {
    if (started) return ESP_ERR_INVALID_STATE;
    mesh_root_recovery_init(&root_recovery);
    uint64_t node_id;
    esp_err_t identity_err = node_identity_get_id(&node_id);
    if (identity_err != ESP_OK) return identity_err;
    esp_err_t store_err = mesh_recovery_store_load(&recovery_record);
    if (store_err != ESP_OK)
        ESP_LOGW(TAG, "Recovery budget read failed; restart disabled: %s", esp_err_to_name(store_err));
    mesh_recovery_init(&recovery, recovery_record.spent != 0, node_id);
    recovery.reason = (omk_mesh_recovery_reason_t)recovery_record.reason;
    if (!mesh_credentials_test_vector_matches()) return ESP_FAIL;
    omk_gateway_credentials_t gateway = {0};
    esp_err_t err = gateway_credentials_load(&gateway);
    if (err != ESP_OK) return err;
    uint8_t mesh_id[6];
    char mesh_password[OMK_MESH_AP_PASSWORD_LENGTH + 1];
    err = mesh_credentials_derive(gateway.ssid, gateway.ssid_length, gateway.psk, gateway.psk_length,
                                  mesh_id, mesh_password);
    if (err != ESP_OK) return err;
    err = esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, ip_event_handler, NULL);
    if (err != ESP_OK) return err;
    err = esp_wifi_set_ps(WIFI_PS_NONE);
    if (err != ESP_OK) return err;
    err = esp_wifi_start();
    if (err != ESP_OK) return err;
    err = esp_mesh_init();
    if (err != ESP_OK) return err;
    err = disable_root_conflicts();
    if (err != ESP_OK) return err;
    err = configure_parent_rssi_thresholds();
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
    log_parent_switch_parameters();
    err = mesh_netif_start_receive_task();
    if (err != ESP_OK) return err;
    err = esp_event_handler_register(OMK_MESH_CONTROL_EVENT, ESP_EVENT_ANY_ID, status_event_handler, NULL);
    if (err != ESP_OK) return err;
    esp_timer_handle_t status_timer;
    const esp_timer_create_args_t timer_config = {.callback = status_timer_callback, .name = "mesh_status"};
    err = esp_timer_create(&timer_config, &status_timer);
    if (err != ESP_OK) return err;
    err = esp_timer_start_periodic(status_timer, OMK_MESH_STATUS_INTERVAL_MS * 1000ULL);
    if (err != ESP_OK) return err;
    started = true;
    ESP_LOGI(TAG, "ESP-WIFI-MESH started");
    return ESP_OK;
}
