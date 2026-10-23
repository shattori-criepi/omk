#include "mesh_network.h"
#include "network_diagnostics.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdatomic.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

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
static atomic_bool is_rootless;
static bool root_role_known;
static bool previous_is_root;
static atomic_bool started;
static atomic_bool parent_connected;
static esp_ip4_addr_t current_ip;
/* Short RAM critical sections only; no MQTT/Mesh/NVS/logging under this lock. */
static portMUX_TYPE recovery_lock = portMUX_INITIALIZER_UNLOCKED;
static omk_mesh_recovery_input_t recovery_snapshot;
static bool recovery_action_pending;
static bool recovery_action_attempted;
static uint32_t recovery_action_ms;
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

static esp_err_t configure_parent_switch_parameters(void) {
    mesh_switch_parent_t configured = {0};
    esp_err_t err = esp_mesh_get_switch_parent_paras(&configured);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not get Mesh parent switching parameters: %s", esp_err_to_name(err));
        return err;
    }
    /* Change only these two trial thresholds; retain every other IDF runtime
     * field, including duration_ms and the select_rssi/switch_rssi pair. */
    configured.cnx_rssi = -78;
    configured.backoff_rssi = -78;
    err = esp_mesh_set_switch_parent_paras(&configured);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not set Mesh parent switching parameters: %s", esp_err_to_name(err));
        return err;
    }

    mesh_switch_parent_t applied = {0};
    err = esp_mesh_get_switch_parent_paras(&applied);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Mesh parent switching read-back failed: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "Mesh parent switching (read-back): self_organized=%d duration_ms=%d"
             " cnx_rssi=%d select_rssi=%d switch_rssi=%d backoff_rssi=%d",
             esp_mesh_get_self_organized(), applied.duration_ms, applied.cnx_rssi,
             applied.select_rssi, applied.switch_rssi, applied.backoff_rssi);
    if (applied.cnx_rssi != configured.cnx_rssi || applied.backoff_rssi != configured.backoff_rssi ||
        applied.duration_ms != configured.duration_ms || applied.select_rssi != configured.select_rssi ||
        applied.switch_rssi != configured.switch_rssi) {
        ESP_LOGE(TAG, "Mesh parent switching read-back does not match requested parameters");
        return ESP_FAIL;
    }
    return ESP_OK;
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

/* Keep root voting in the periodic control path, rather than in a disconnect event:
 * a request observed while disconnected remains pending until the root has a
 * parent again. */
static void try_root_recovery(void) {
    uint32_t now_ms = uptime_milliseconds();
    bool is_root = esp_mesh_is_root();
    portENTER_CRITICAL(&recovery_lock);
    bool execute = mesh_root_recovery_should_execute(
        &root_recovery, now_ms, is_root, parent_connected, is_rootless);
    portEXIT_CRITICAL(&recovery_lock);
    if (!execute || !esp_mesh_is_root()) return;
    const char *reason = "root_link_unhealthy";
    ESP_LOGI(TAG, "root reelection requested: %s (level=1)", reason);
    esp_err_t err = esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "root reelection deferred: %s (%s)", reason, esp_err_to_name(err));
        return;
    }
    portENTER_CRITICAL(&recovery_lock);
    mesh_root_recovery_mark_executed(&root_recovery, uptime_milliseconds());
    portEXIT_CRITICAL(&recovery_lock);
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
        .mqtt_progress = mqtt_registration_get_progress().ack_seen,
    };
}

static void observe_recovery(void) {
    omk_mesh_recovery_input_t in = recovery_input();
    portENTER_CRITICAL(&recovery_lock);
    recovery_snapshot = in;
    mesh_recovery_observe(&recovery, uptime_milliseconds(), &in);
    portEXIT_CRITICAL(&recovery_lock);
}

/* Must be called under recovery_lock; esp_mesh_is_root is a RAM-only read. */
static void observe_control_role_locked(void) {
    omk_mesh_recovery_input_t in = recovery_snapshot;
    in.is_root = esp_mesh_is_root();
    mesh_recovery_observe(&recovery, uptime_milliseconds(), &in);
}

static void run_recovery_action(omk_mesh_recovery_action_t action, uint32_t now_ms,
                                const omk_mesh_recovery_token_t *selection) {
    (void)now_ms;
    bool control = action == OMK_RECOVERY_CHILD_RESELECT ||
                   action == OMK_RECOVERY_ROUTER_RECONNECT || action == OMK_RECOVERY_ROOT_VOTE;
    if (control) {
        portENTER_CRITICAL(&recovery_lock);
        observe_control_role_locked();
        bool valid = selection && selection->action == action &&
                     mesh_recovery_action_matches(&recovery, selection);
        portEXIT_CRITICAL(&recovery_lock);
        if (!valid) return;
    }
    esp_err_t err = ESP_OK;
    switch (action) {
    case OMK_RECOVERY_CHILD_RESELECT:
        /* Revalidate role immediately before a potentially disruptive API. */
        portENTER_CRITICAL(&recovery_lock);
        bool wait_ip = recovery_snapshot.started && !recovery_snapshot.has_ip;
        portEXIT_CRITICAL(&recovery_lock);
        if (esp_mesh_is_root() || (parent_connected && !is_rootless && !wait_ip)) return;
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
    case OMK_RECOVERY_ROOT_VOTE:
        portENTER_CRITICAL(&recovery_lock);
        recovery.reason = OMK_RECOVERY_ROOT_LINK;
        portEXIT_CRITICAL(&recovery_lock);
        try_root_recovery();
        return;
    default:
        return;
    }
    portENTER_CRITICAL(&recovery_lock);
    if (control) observe_control_role_locked();
    /* Validation and application share the lock: an event/monitor cannot reset
     * the episode between this check and explicit_accepted being written. */
    if (!control || mesh_recovery_action_matches(&recovery, selection))
        mesh_recovery_complete(&recovery, uptime_milliseconds(), action, err == ESP_OK);
    omk_mesh_recovery_t state = recovery;
    portEXIT_CRITICAL(&recovery_lock);
    ESP_LOGI(TAG, "Mesh recovery action=%d reason=%s request=%s stage=%s",
             action, mesh_recovery_reason_name(state.reason), esp_err_to_name(err),
             mesh_recovery_stage_name(state.stage));
}

static void try_recovery(void) {
    uint32_t token = network_diag_begin(ND_RECOVERY);
    uint32_t now_ms = uptime_milliseconds();
    portENTER_CRITICAL(&recovery_lock);
    omk_mesh_recovery_input_t in = recovery_snapshot;
    portEXIT_CRITICAL(&recovery_lock);
    in.is_root = esp_mesh_is_root();
    in.mqtt_started = mqtt_registration_is_started();
    in.mqtt_connected = mqtt_registration_is_connected();
    in.mqtt_progress = mqtt_registration_get_progress().ack_seen;
    portENTER_CRITICAL(&recovery_lock);
    in.started = recovery_snapshot.started;
    in.parent_connected = recovery_snapshot.parent_connected;
    in.rootless = recovery_snapshot.rootless;
    in.has_ip = recovery_snapshot.has_ip;
    /* esp_mesh_is_root is a RAM-only role read in the pinned IDF. */
    in.is_root = esp_mesh_is_root();
    /* An event can have reset a policy timer while we sampled MQTT above. */
    now_ms = uptime_milliseconds();
    omk_mesh_recovery_action_t action = mesh_recovery_choose(&recovery, now_ms, &in, &root_recovery);
    omk_mesh_recovery_token_t selection = mesh_recovery_action_token(&recovery, action);
    omk_mesh_recovery_stage_t stage = recovery.stage;
    bool deferred = action == OMK_RECOVERY_CHILD_RESELECT ||
                    action == OMK_RECOVERY_ROUTER_RECONNECT || action == OMK_RECOVERY_ROOT_VOTE;
    if (deferred) {
        /* The observer is faster; disruptive API retries retain the original
         * 30-second cadence, even when an API fails immediately. */
        if (recovery_action_pending || (recovery_action_attempted &&
            (uint32_t)(now_ms - recovery_action_ms) < OMK_MESH_STATUS_INTERVAL_MS)) {
            action = OMK_RECOVERY_NONE;
        } else {
            recovery_action_pending = recovery_action_attempted = true;
            recovery_action_ms = now_ms;
        }
    }
    portEXIT_CRITICAL(&recovery_lock);
    network_diag_recovery((in.started ? 1U : 0U) | (in.is_root ? 2U : 0U) |
                         (in.parent_connected ? 4U : 0U) | (in.rootless ? 8U : 0U) |
                         (in.has_ip ? 16U : 0U) | (in.mqtt_started ? 32U : 0U) |
                         (in.mqtt_connected ? 64U : 0U) | /* retired budget bit 128 stays zero */
                         (in.mqtt_progress ? 256U : 0U), stage, action);
    if (deferred && action != OMK_RECOVERY_NONE) {
        if (esp_event_post(OMK_MESH_CONTROL_EVENT, 1, &selection, sizeof(selection), 0) != ESP_OK) {
            portENTER_CRITICAL(&recovery_lock);
            recovery_action_pending = false;
            portEXIT_CRITICAL(&recovery_lock);
        }
    }
    network_diag_end(token, 0, false);
}

static void recovery_task(void *argument) {
    (void)argument;
    for (;;) {
        /* No MQTT mutex or network send/receive APIs in this task. */
        try_recovery();
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

static void publish_recovery_status(uint64_t node_id) {
    portENTER_CRITICAL(&recovery_lock);
    omk_mesh_recovery_t state = recovery;
    omk_mesh_recovery_record_t record = recovery_record;
    portEXIT_CRITICAL(&recovery_lock);
    omk_mqtt_progress_t progress = mqtt_registration_get_progress();
    /* Compatibility: retired budget/retry flags are always false; the count
     * and last_restart_reason are read-only history from older firmware. */
    /* A companion status avoids overflowing or removing existing 1152-byte
     * node/status fields. Counters survive reconnection; the restart record
     * also survives reboot. No backlog is accumulated while disconnected. */
    /* Default event-loop stack is small. This buffer has one serialized owner. */
    static char payload[1024];
    int n = snprintf(payload, sizeof(payload),
        "{\"node_id\":\"%012" PRIx64 "\",\"uptime_s\":%" PRIu32
        ",\"recovery_stage\":\"%s\",\"last_recovery_reason\":\"%s\""
        ",\"no_parent_found_count\":%" PRIu32 ",\"last_scan_times\":%d"
        ",\"stop_reconnection_count\":%" PRIu32 ",\"parent_reselection_count\":%" PRIu32
        ",\"parent_loss_duration_s\":%" PRIu32 ",\"restart_budget_spent\":%s"
        ",\"recovery_restart_count\":%" PRIu32 ",\"last_restart_reason\":\"%s\""
        ",\"mqtt_progress_fresh\":%s,\"mqtt_probe_pending_id\":%d"
        ",\"mqtt_probe_ack_count\":%" PRIu32 ",\"mqtt_probe_last_ack_ms\":%" PRIu32
        ",\"restart_retry_ready\":%s,\"restart_persistence_blocked\":%s}",
        node_id, uptime_seconds(), mesh_recovery_stage_name(state.stage),
        mesh_recovery_reason_name(state.reason), state.no_parent_found_count,
        state.last_scan_times, state.stop_reconnection_count, state.parent_reselection_count,
        mesh_recovery_loss_duration_s(&state, uptime_milliseconds()),
        "false", record.restart_count,
        mesh_recovery_reason_name((omk_mesh_recovery_reason_t)record.reason),
        progress.ack_seen ? "true" : "false", progress.pending_id, progress.ack_count, progress.ack_ms,
        "false", "false");
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
    bool is_root = esp_mesh_is_root();
    portENTER_CRITICAL(&recovery_lock);
    mesh_root_recovery_observe_link(&root_recovery, uptime_milliseconds(), is_root,
                                    rssi_valid, rssi, parent_disconnect_count,
                                    mqtt_registration_get_disconnect_count());
    portEXIT_CRITICAL(&recovery_lock);
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
        if (!mesh_netif_accept_ip_event(data, false)) return;
        current_ip.addr = 0;
        observe_recovery();
        return;
    }
    if (id != IP_EVENT_STA_GOT_IP) return;
    ip_event_got_ip_t *event = data;
    if (!mesh_netif_accept_ip_event(event, true)) return;
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
        observe_recovery();
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
        current_ip.addr = 0;
        observe_recovery();
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
        portENTER_CRITICAL(&recovery_lock);
        mesh_recovery_note_no_parent(&recovery, event != NULL ? event->scan_times : -1);
        omk_mesh_recovery_t state = recovery;
        portEXIT_CRITICAL(&recovery_lock);
        ESP_LOGW(TAG, "Mesh no parent found: count=%" PRIu32 " scan_times=%d stage=%s",
                 state.no_parent_found_count, state.last_scan_times, mesh_recovery_stage_name(state.stage));
        break;
    }
    case MESH_EVENT_STOP_RECONNECTION: {
        observe_recovery();
        portENTER_CRITICAL(&recovery_lock);
        mesh_recovery_note_stopped(&recovery);
        omk_mesh_recovery_t state = recovery;
        portEXIT_CRITICAL(&recovery_lock);
        ESP_LOGW(TAG, "Mesh stopped reconnection: count=%" PRIu32 " stage=%s",
                 state.stop_reconnection_count, mesh_recovery_stage_name(state.stage));
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
    observe_recovery();
}

/* Timer callbacks never race Mesh/IP handlers or block the ESP timer task. */
static void status_timer_callback(void *argument) {
    (void)argument;
    uint64_t posted_us = esp_timer_get_time();
    network_diag_tick(posted_us);
    esp_err_t err = esp_event_post(OMK_MESH_CONTROL_EVENT, 0, NULL, 0, 0);
    network_diag_post(posted_us, err);
    if (err != ESP_OK) ESP_LOGW(TAG, "Mesh status cycle deferred: %s", esp_err_to_name(err));
}

static void status_event_handler(void *argument, esp_event_base_t base, int32_t id, void *data) {
    (void)base;
    if (id == 1 && data != NULL) {
        const omk_mesh_recovery_token_t *selection = data;
        run_recovery_action(selection->action, uptime_milliseconds(), selection);
        portENTER_CRITICAL(&recovery_lock);
        recovery_action_pending = false;
        portEXIT_CRITICAL(&recovery_lock);
        return;
    }
    uint32_t token = network_diag_begin(ND_CONTROL);
    network_diag_control(true);
    publish_status(argument);
    network_diag_control(false);
    network_diag_end(token, 0, false);
}

esp_err_t mesh_network_start_prepared(void) {
    if (started) return ESP_ERR_INVALID_STATE;
    mesh_root_recovery_init(&root_recovery);
    uint64_t node_id;
    esp_err_t identity_err = node_identity_get_id(&node_id);
    if (identity_err != ESP_OK) return identity_err;
    esp_err_t store_err = mesh_recovery_store_load(&recovery_record);
    if (store_err != ESP_OK)
        ESP_LOGW(TAG, "Historical recovery record unavailable: %s", esp_err_to_name(store_err));
    /* Legacy reservations are diagnostic history only; never feed the arbiter. */
    mesh_recovery_init(&recovery, node_id);
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
    /* Bound one slow peer in the isolated sender; retain reliable P2P. */
    (void)esp_mesh_send_block_time(5000);
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
    err = configure_parent_switch_parameters();
    if (err != ESP_OK) return err;
    err = esp_mesh_start();
    if (err != ESP_OK) return err;
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
    portENTER_CRITICAL(&recovery_lock);
    recovery_snapshot.started = true;
    portEXIT_CRITICAL(&recovery_lock);
    if (xTaskCreate(recovery_task, "mesh_recovery", 4096, NULL, 5, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    ESP_LOGI(TAG, "ESP-WIFI-MESH started");
    return ESP_OK;
}
