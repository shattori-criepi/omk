"""Drive the production transport, not a duplicate role/config implementation."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

BASE = Path(__file__).resolve().parents[1]
CJSON_LIMIT = next(line.split("=", 1)[1] for line in (BASE / "sdkconfig.atom-s3-lite").read_text().splitlines() if line.startswith("CONFIG_CJSON_NESTING_LIMIT="))


@pytest.fixture(scope="module", params=[0, 1], ids=["diag-off", "diag-hooks-on"])
def events_host(tmp_path_factory, request):
    work = tmp_path_factory.mktemp("native-events")
    cjson = BASE / "managed_components/espressif__cjson/cJSON"
    if not cjson.exists():
        cjson = Path(os.environ["OMK_TEST_CJSON_DIR"])
    shutil.copyfile(BASE / "tests/native_events_host/stubs.h", work / "stubs.h")
    for name in ("esp_err.h", "esp_event.h", "esp_log.h", "esp_mac.h", "esp_mesh.h",
                 "esp_netif.h", "esp_netif_net_stack.h", "lwip/dhcp.h",
                 "esp_timer.h", "esp_random.h", "esp_wifi.h", "esp_wifi_default.h",
                 "esp_wifi_types.h", "mqtt_client.h", "freertos/FreeRTOS.h",
                 "freertos/queue.h", "freertos/task.h"):
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "stubs.h"\n')
    # Exercise the unchanged policy/protocol suite with hooks compiled in too.
    # Real core and USB-owner persistence are tested separately with SDK doubles.
    hooks = work / "diag_hooks.c"
    hooks.write_text(r'''
#include <assert.h>
#include "mesh_diag.h"
#if OMK_DIAGNOSTICS
static int sending;
unsigned diag_kind8_begin, diag_kind8_end;
static unsigned active_kind;
void mesh_diag_request(unsigned kind) { (void)kind; }
void mesh_diag_ble(unsigned t,bool e,bool r,bool s) { (void)t;(void)e;(void)r;(void)s; }
void mesh_diag_tx_begin(const mesh_wire_message_t *m, bool upstream) { (void)upstream; assert(m && !sending); sending=1; active_kind=m->kind; if(active_kind==8) ++diag_kind8_begin; }
void mesh_diag_tx_end(int32_t result) { (void)result; assert(sending); if(active_kind==8) ++diag_kind8_end; sending=0; }
void mesh_diag_mesh_event(int32_t id) { (void)id; }
void mesh_diag_disconnected(const uint8_t b[6], uint8_t r, int8_t s) { assert(b); (void)r; (void)s; }
void mesh_diag_quality(bool bad) { (void)bad; }
void mesh_diag_demand(bool tx,bool delivery,uint64_t proven,uint64_t inhibit,bool voting) { (void)tx;(void)delivery;(void)proven;(void)inhibit;(void)voting; }
void mesh_diag_recovery(uint32_t action,uint32_t reasons,bool finished,int32_t result) { (void)action;(void)reasons;(void)finished;(void)result; }
void mesh_diag_receipt(const char *node,const char *boot,uint32_t seq) { (void)node;(void)boot;(void)seq; }
void mesh_diag_root(const mesh_wire_message_t *m,unsigned stage) { assert(!m || m->kind!=8); assert(stage < DIAG_STAGE_COUNT); }
#endif
''')
    executable = work / "events"
    subprocess.run([
        "cc", "-std=c11", "-DOMK_DIAGNOSTICS=" + str(request.param), "-DCJSON_NESTING_LIMIT=" + CJSON_LIMIT, "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        "-fsanitize=undefined", "-fno-sanitize-recover=all",
        "-I", str(work), "-I", str(BASE / "src"), "-I", str(cjson),
        str(BASE / "tests/native_events_host/test.c"), str(hooks),
        str(BASE / "src/mesh_session.c"), str(BASE / "src/mesh_wire.c"),
        str(BASE / "src/native_recovery.c"),
        *[str(BASE/"src"/n) for n in ("ble_scan_trial.c","ble_scan_command.c","ble_scan_metrics.c","ble_scan_runtime.c")],
        str(cjson / "cJSON.c"), "-lm", "-o", str(executable),
    ], check=True, text=True)
    return executable


@pytest.mark.parametrize("case", [
    "v2_quiet_aged", "v2_quiet_parent", "v2_quiet_root", "v2_quiet_config", "v2_quiet_vote", "v2_gate_exclusion", "v2_busy_non_tx",
    "v2_busy", "v2_root_race", "v2_api_fail", "v2_root_during_cut", "v2_disconnect_missing",
    "v2_restore_failure", "v2_link_missing", "v2_verify_failure", "v2_m1", "v2_recovered",
    "scan_pacing_active", "scan_pacing_baseline", "scan_schedule_reporting", "scan_topology_quiet", "scan_stale_topology", "scan_m1_recovery", "scan_runtime_resume", "scan_m1_ok", "scan_m1_error", "scan_m1_reject", "scan_m1_publish", "scan_m1_bridge", "scan_topic", "scan_nonce", "scan_nonce_race",
    "lr2_observed_nomem", "lr2_observed_nomem_api_fail", "lr2_root_work", "lr2_blocked_return",
    "lr2_nomem", "lr2_success", "lr2_parent", "lr2_root", "lr2_config", "lr2_recovery", "lr2_tx_stall",
    "lr2_root_address", "lr2_root_learned", "lr2_rootless",
    "sdk_upstream_contract", "ab5_vote_timeout", "ab5_vote_stopped", "ab5_delay_119500", "ab5_delay_120000",
    "ab5_boundary_unknown_valid", "ab5_boundary_unknown_expired",
    "ab5_boundary_known_valid", "ab5_boundary_known_expired", "ab5_fractional_progress",
    "ab4_delay_0", "ab4_delay_31", "ab4_delay_60", "ab4_delay_90", "ab4_delay_119", "ab4_delay_120", "ab4_burst", "ab4_vote", "ab4_sdk_layer", "ab4_sdk_layer_mismatch", "ab4_sdk_parent", "ab4_sdk_error", "ab4_sdk_root", "ab4_sdk_race", "ab4_source", "ab4_order", "ab4_expiry", "ab4_unsent",
    "rootmac_no_event_healthy", "rootmac_latest_only", "rootmac_invalid_unknown",
    "rootmac_reconnect_same", "rootmac_reconnect_new", "rootmac_rootless", "rootmac_role", "rootmac_restart",
    "rootmac_silent_change", "rootmac_event_before_parent", "rootmac_event_after_receipt", "rootmac_vote",
    "rootmac_config_generation", "rootmac_race", "rootmac_before_send_return", "rootmac_wrap",
    "rootmac_config_source", "rootmac_real_loss",
    "review_unknown_reconnect", "review_unknown_rootless", "review_unknown_restored",
    "review_vote_lost", "review_vote_rootless", "review_vote_mqtt", "review_vote_role", "review_vote_parent",
    "review_vote_disconnect", "review_vote_race", "review_vote_duplicate", "review_vote_query_failure", "review_config_worker",
    "review_status_busy", "review_fair_local", "review_fair_root", "review_status_latest", "review_json_depth",
    "review_log_progress", "review_log_epoch", "review_log_config", "review_log_vote", "review_log_receipt",
    "review_origin_config", "review_origin_link", "review_origin_role",
    "delivery_sdk_success", "delivery_jitter", "delivery_both", "delivery_healthy", "delivery_short",
    "delivery_idle", "delivery_burst", "delivery_sample_gap", "delivery_budget", "delivery_congestion",
    "delivery_shared_budget", "delivery_legacy_weak",
    "delivery_legacy_disconnect", "delivery_legacy_rootless", "delivery_sdk_race", "delivery_early_ack",
    "delivery_cancel_a", "delivery_cancel_b", "delivery_cancel_both", "delivery_delayed", "delivery_transit_delay",
    "delivery_duplicate", "delivery_wrap", "delivery_invalid", "delivery_reconnect", "delivery_root_change",
    "delivery_config", "delivery_unknown_root", "delivery_time64", "delivery_inhibit",
    "receipt_no_mqtt", "receipt_mqtt_full", "receipt_queue_full", "receipt_rx_full", "receipt_invalid",
    "receipt_stale", "receipt_epoch", "receipt_demote", "receipt_root_local",
    "delivery_roundtrip",
    "tx_queue", "tx_queue_jitter", "tx_idle", "tx_burst", "tx_errors", "tx_short",
    "tx_sdk_success_without_delivery", "tx_blocked_call", "tx_progress_race",
    "tx_epoch_race", "tx_role_race", "tx_config_race", "tx_idle_race",
    "tx_api_failure_budget", "tx_reconnect_budget", "tx_success_budget",
    "tx_late_epoch", "tx_late_config", "tx_late_role", "tx_late_rootless",
    "tx_root", "tx_voting", "tx_inhibit", "tx_disconnected", "tx_rootless",
    "tx_invalid", "tx_time64", "tx_sample_gap",
    "ble_child_bridge", "ble_roles", "policy", "policy_error", "policy_readback", "rootless_ip", "stale_ip",
    "epoch_race", "role_switch", "diagnostics", "io_counters",
    "same_lease", "different_lease", "rootless_lease", "lease_guards",
    "lease_races", "dhcp_transition", "root_mac_lifetime", "lease_interval",
    "rec_vote", "rec_vote_boundary", "rec_vote_threshold", "rec_vote_good",
    "rec_vote_zero", "rec_vote_invalid", "rec_vote_bad_bssid", "rec_vote_fail",
    "rec_vote_other_router", "rec_vote_ack", "rec_vote_epoch", "rec_vote_role",
    "rec_vote_rootless", "rec_vote_disconnect", "rec_vote_ack_race", "rec_vote_voting", "rec_vote_no_demand",
    "rec_window", "rec_window_reset",
    "rec_parent", "rec_parent_natural", "rec_parent_stale", "rec_parent_promote", "rec_parent_failure",
    "rec_rootless", "rec_rootless_cleared", "rec_router", "rec_router_reconnected", "rec_router_demoted", "rec_router_failure",
    "rec_dhcp", "rec_dhcp_running", "rec_dhcp_stale", "rec_dhcp_lease_race", "rec_dhcp_failure",
    "rec_mqtt_stall", "rec_mqtt_natural", "rec_broker_down", "rec_mqtt_epoch",
    "rec_mqtt_ack_race", "rec_mqtt_role_race", "rec_mqtt_no_demand", "rec_mqtt_rootless",
    "rec_mqtt_recreate_race", "rec_mqtt_normal_reconnect", "rec_mqtt_idle_demand", "rec_mqtt_disconnect_grace", "rec_mqtt_reconnect_stall",
    "rec_repeat_root", "rec_repeat_root_jitter", "rec_repeat_root_rootless",
    "rec_repeat_root_other", "rec_repeat_root_role", "rec_repeat_root_expired", "rec_repeat_root_failure",
    "rec_repeat_child", "rec_repeat_child_changed", "rec_repeat_child_other",
    "rec_repeat_child_role", "rec_repeat_child_expired", "rec_repeat_child_failure",
    "rec_repeat_child_epoch_race", "rec_repeat_child_promote", "rec_repeat_child_callback",
    "rec_repeat_child_topology",
    "rec_loss_root", "rec_loss_child", "rec_loss_clock_root", "rec_loss_clock_child",
    "rec_mqtt_flap", "rec_mqtt_flap_jitter", "rec_mqtt_flap_progress",
    "rec_mqtt_flap_ack_race", "rec_mqtt_flap_failure", "rec_mqtt_budget_disconnected",
    "rec_mqtt_demand_epoch",
])
def test_native_transport_events(events_host, case):
    subprocess.run([str(events_host), case], check=True, timeout=10)
