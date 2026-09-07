import subprocess
import textwrap
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_mesh_mqtt_liveness_policy_boundaries(tmp_path):
    program = tmp_path / "mesh_liveness_test.c"
    executable = tmp_path / "mesh_liveness_test"
    program.write_text(textwrap.dedent("""
        #include <assert.h>
        #include "mesh_liveness.h"

        int main(void) {
            omk_mesh_mqtt_liveness_state_t state = {
                .mesh_started = true, .parent_connected = true,
                .has_ip = true, .normal_operation = true,
                .mqtt_disconnected_duration_s = 180,
            };
            assert(mesh_mqtt_liveness_should_recover(&state));
            state.mqtt_connected = true;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            state.mqtt_connected = false;
            state.mqtt_disconnected_duration_s = 179;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            state.mqtt_disconnected_duration_s = 180;
            state.parent_connected = false;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            state.parent_connected = true;
            state.rootless = true;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            state.rootless = false;
            state.has_ip = false;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            state.has_ip = true;
            state.normal_operation = false;
            assert(!mesh_mqtt_liveness_should_recover(&state));
            return 0;
        }
    """))
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Werror", "-I", str(SOURCE),
         str(program), str(SOURCE / "mesh_liveness.c"), "-o", str(executable)],
        check=True,
    )
    subprocess.run([str(executable)], check=True)


def test_watchdog_uses_timer_context_and_persists_reason_before_restart():
    source = (SOURCE / "mesh_network.c").read_text()
    assert "#define OMK_MESH_MQTT_LIVENESS_TIMEOUT_S 180U" in (SOURCE / "mesh_liveness.h").read_text()
    assert "mesh_mqtt_liveness_should_recover(&state)" in source
    assert 'boot_diagnostics_record_restart_reason("mesh_mqtt_liveness_timeout")' in source
    assert "esp_restart();" in source
    assert "mqtt_liveness_since_us = 0;" in source
    assert "eligible_while_disconnected" in source
    timer = source[source.index("static void publish_status") : source.index("static void ip_event_handler")]
    assert timer.index("check_mqtt_liveness();") > timer.index("mqtt_registration_publish_mesh_status(payload)")


def test_status_payload_has_capacity_for_liveness_and_mesh_data_plane_diagnostics():
    source = (SOURCE / "mesh_network.c").read_text()
    mqtt = (SOURCE / "mqtt_registration.c").read_text()
    assert "#define OMK_MESH_STATUS_PAYLOAD_SIZE 1152" in source
    assert "#define OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE 1152" in mqtt
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


def test_restart_reason_is_persisted_and_exposed_on_next_boot():
    source = (SOURCE / "boot_diagnostics.c").read_text()
    assert 'OMK_LAST_OMK_RESTART_REASON_KEY "last_omk_reason"' in source
    assert "nvs_set_str(nvs, OMK_LAST_OMK_RESTART_REASON_KEY, reason)" in source
    assert "nvs_get_str(nvs, OMK_LAST_OMK_RESTART_REASON_KEY" in source
    assert "OMK_MESH_MQTT_LIVENESS_RESTART_COUNT_KEY" in source
