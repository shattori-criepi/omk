import subprocess
import textwrap
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_mesh_mqtt_liveness_policy_boundaries(tmp_path):
    program = tmp_path / "mesh_liveness_test.c"
    executable = tmp_path / "mesh_liveness_test"
    program.write_text(textwrap.dedent("""
        #include <assert.h>
        #include "mesh_recovery.h"
        int main(void) {
            omk_mesh_recovery_t policy;
            omk_mesh_root_recovery_t root;
            mesh_recovery_init(&policy, 1);mesh_root_recovery_init(&root);
            omk_mesh_recovery_input_t in={.started=true,.parent_connected=true,
                .has_ip=true,.mqtt_started=true};
            // Broker down, then connected without PUBACK: neither creates an action.
            for(unsigned connected=0;connected<2;connected++) {
                in.mqtt_connected=connected;
                for(uint32_t t=0;t<=86400000;t+=30000)
                    assert(mesh_recovery_choose(&policy,t,&in,&root)==OMK_RECOVERY_NONE);
                assert(policy.stage==OMK_RECOVERY_MQTT_WAIT);
            }
            in.mqtt_progress=true;
            assert(mesh_recovery_choose(&policy,86430000,&in,&root)==OMK_RECOVERY_NONE);
            assert(policy.stage==OMK_RECOVERY_HEALTHY);
            in.started=false;
            assert(mesh_recovery_choose(&policy,86460000,&in,&root)==OMK_RECOVERY_NONE);
            assert(policy.stage==OMK_RECOVERY_IDLE);
        }
    """))
    subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(SOURCE),
                    str(program), str(SOURCE / "mesh_recovery.c"),
                    str(SOURCE / "mesh_root_recovery.c"), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


def test_recovery_is_serialized_with_events_without_restart_budget():
    source = (SOURCE / "mesh_network.c").read_text()
    assert ".callback = status_timer_callback" in source
    assert "esp_event_post(OMK_MESH_CONTROL_EVENT" in source
    assert "status_event_handler" in source
    for retired in ("esp_restart(", "mesh_recovery_store_save", "restart_selection", "rearm_attempt"):
        assert retired not in source
    policy = (SOURCE / "mesh_recovery.c").read_text()
    assert "OMK_RECOVERY_RESTART" not in policy and "OMK_RECOVERY_REARM" not in policy
    assert "restart_spent" not in policy
    assert "mesh_recovery_action_matches" in source


def test_status_payload_has_capacity_for_liveness_and_mesh_data_plane_diagnostics():
    source = (SOURCE / "mesh_network.c").read_text()
    mqtt = (SOURCE / "mqtt_registration.c").read_text()
    assert "#define OMK_MESH_STATUS_PAYLOAD_SIZE 1152" in source
    assert "#define OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE 2816" in mqtt
    for field in (
        "mqtt_connected", "mqtt_disconnected_duration_s", "mqtt_last_connected_uptime_s",
        "mesh_rx_success_count", "mesh_tx_success_count", "mesh_tx_failure_count",
        "mesh_last_rx_success_uptime_s", "mesh_last_tx_success_uptime_s",
        "last_omk_restart_reason", "mesh_mqtt_liveness_restart_count",
    ):
        assert field in source
    payload = {
        "node_id": "f" * 12, "mesh_layer": -2147483648, "is_root": False,
        "parent_bssid": "ff:ff:ff:ff:ff:ff", "rssi_dbm": -2147483648,
        "rssi_valid": False, "ip": "255.255.255.255",
        "parent_change_count": 4294967295, "parent_disconnect_count": 4294967295,
        "is_rootless": False, "rootless_duration_s": 4294967295,
        "last_parent_disconnect_reason": 4294967295,
        "last_wifi_disconnect_reason": 4294967295, "root_switch_count": 4294967295,
        "mqtt_disconnect_count": 4294967295, "mqtt_connected": False,
        "mqtt_disconnected_duration_s": 4294967295,
        "mqtt_last_connected_uptime_s": 4294967295,
        "mesh_rx_success_count": 4294967295, "mesh_tx_success_count": 4294967295,
        "mesh_tx_failure_count": 4294967295,
        "mesh_last_rx_success_uptime_s": 4294967295,
        "mesh_last_tx_success_uptime_s": 4294967295,
        "uptime_s": 4294967295, "free_heap_bytes": 4294967295,
        "minimum_free_heap_bytes": 4294967295, "reset_reason": "interrupt_watchdog",
        "reset_reason_code": 4294967295, "boot_count": 4294967295,
        "last_omk_restart_reason": "mesh_mqtt_liveness_timeout",
        "mesh_mqtt_liveness_restart_count": 4294967295,
    }
    import json
    assert len(json.dumps(payload, separators=(",", ":"))) < 1152


def test_historical_restart_reason_is_read_without_new_communication_writes():
    source = (SOURCE / "boot_diagnostics.c").read_text()
    assert 'OMK_LAST_OMK_RESTART_REASON_KEY "last_omk_reason"' in source
    assert "nvs_set_str" not in source
    assert "nvs_set_u32(nvs, OMK_MESH_MQTT_LIVENESS_RESTART_COUNT_KEY" not in source
    assert "nvs_get_str(nvs, OMK_LAST_OMK_RESTART_REASON_KEY" in source
    assert "OMK_MESH_MQTT_LIVENESS_RESTART_COUNT_KEY" in source
