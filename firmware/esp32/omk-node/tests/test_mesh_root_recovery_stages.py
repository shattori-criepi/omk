"""Run both recovery stages and the real API dispatch/liveness path with host stubs."""
import subprocess
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def recovery_binary(tmp_path_factory):
    source = (SOURCE / "mesh_network.c").read_text()
    dispatch = source[source.index("static void try_root_recovery(void)"):
                      source.index("void mesh_network_log_diagnostics")]
    liveness = source[source.index("static void check_mqtt_liveness(void)"):
                      source.index("/* The first observed role")]
    harness = r'''
        #include <assert.h>
        #include <stdbool.h>
        #include <stdint.h>
        #include <stdio.h>
        #include <string.h>
        #include "mesh_root_recovery.h"
        #include "mesh_liveness.h"

        typedef int esp_err_t;
        #define ESP_OK 0
        #define ESP_FAIL (-1)
        #define MESH_VOTE_REASON_ROOT_INITIATED 1
        #define TAG "test"
        #define ESP_LOGI(tag, ...) do { (void)(tag); printf(__VA_ARGS__); puts(""); } while (0)
        #define ESP_LOGW ESP_LOGI
        static omk_mesh_root_recovery_t root_recovery;
        static uint32_t now_ms;
        static bool root = true, parent_connected = true, is_rootless;
        static bool started = true, mqtt_connected;
        static struct { uint32_t addr; } current_ip = {1};
        static int64_t mqtt_liveness_since_us;
        static unsigned votes, reselections, restarts, recorded_restarts, diagnostics;
        static esp_err_t api_result = ESP_OK;
        static uint32_t uptime_milliseconds(void) { return now_ms; }
        static int64_t esp_timer_get_time(void) { return (int64_t)now_ms * 1000; }
        static bool esp_mesh_is_root(void) { return root; }
        static bool mqtt_registration_is_started(void) { return true; }
        static bool mqtt_registration_is_connected(void) { return mqtt_connected; }
        static const char *esp_err_to_name(esp_err_t err) {
            assert(err == ESP_FAIL);
            return "ESP_FAIL";
        }
        static esp_err_t esp_mesh_waive_root(const void *vote, int reason) {
            assert(root && vote == NULL && reason == MESH_VOTE_REASON_ROOT_INITIATED);
            ++votes;
            return api_result;
        }
        static esp_err_t esp_mesh_set_self_organized(bool enable, bool select_parent) {
            assert(root && enable && select_parent);
            ++reselections;
            return api_result;
        }
        static void mesh_network_log_diagnostics(const char *event, uint32_t reason) {
            assert(strcmp(event, "mesh_mqtt_liveness_timeout: software restart") == 0);
            assert(reason == 0);
            ++diagnostics;
        }
        static void boot_diagnostics_record_restart_reason(const char *reason) {
            assert(strcmp(reason, "mesh_mqtt_liveness_timeout") == 0);
            ++recorded_restarts;
        }
        static void esp_restart(void) {
            assert(diagnostics == restarts + 1 && recorded_restarts == restarts + 1);
            ++restarts;
        }
    ''' + dispatch + liveness + r'''
        static void tick(uint32_t now, bool is_root, bool valid, int rssi,
                         uint32_t parent_count, uint32_t mqtt_count) {
            now_ms = now;
            root = is_root;
            unsigned before = votes + reselections;
            mesh_root_recovery_observe_link(&root_recovery, now, root, valid, rssi,
                                            parent_count, mqtt_count);
            try_root_recovery();
            /* Includes API failures: never fall through to the other stage. */
            assert(votes + reselections <= before + 1);
        }
        static void weak(uint32_t now) { tick(now, true, true, -85, 0, 0); }
        static void sustained(void) {
            for (uint32_t t = 0; t < 180000; t += 30000) {
                weak(t);
                assert(votes == 0 && reselections == 0);
            }
            weak(179999);
            assert(reselections == 0);
            weak(180000);
            assert(votes == 0 && reselections == 1);
            assert(root_recovery.has_parent_reselected);
            assert(root_recovery.last_parent_reselection_ms == 180000);
            assert(!root_recovery.severe_weak_active);
        }
        static void reset_observation(const char *scenario) {
            weak(0);
            weak(150000);
            bool is_root = strcmp(scenario, "child_reset") != 0;
            bool valid = strcmp(scenario, "invalid_reset") != 0;
            int rssi = strcmp(scenario, "rssi_reset") == 0 ? -84 : -90;
            tick(179999, is_root, valid, rssi, 0, 0);
            assert(!root_recovery.severe_weak_active);
            weak(180000);
            weak(359999);
            assert(reselections == 0);
            weak(360000);
            assert(reselections == 1);
        }
        static void invalid(void) {
            for (uint32_t t = 0; t <= 360000; t += 30000)
                tick(t, true, false, -90, 0, 0);
            assert(votes == 0 && reselections == 0);
        }
        static void after_vote(bool becomes_child) {
            tick(0, true, true, -89, 0, 0);
            tick(30000, true, true, -89, 3, 0);
            assert(votes == 1 && reselections == 0);
            assert(root_recovery.severe_weak_since_ms == 0);
            for (uint32_t t = 60000; t <= 180000; t += 30000)
                tick(t, !becomes_child, true, -89, 3, 0);
            assert(votes == 1 && reselections == (becomes_child ? 0U : 1U));
        }
        static void cooldown(void) {
            sustained();
            /* Even if Mesh makes this node root again, don't immediately retry. */
            for (uint32_t t = 210000; t < 1080000; t += 30000) weak(t);
            weak(1079999);
            assert(reselections == 1);
            weak(1080000);
            assert(reselections == 2);
        }
        static void cooldown_then_new_episode(void) {
            sustained();
            tick(210000, false, true, -90, 0, 0);
            weak(1080000);
            weak(1259999);
            assert(reselections == 1);
            weak(1260000);
            assert(reselections == 2);
        }
        static void api_failure(void) {
            weak(0);
            api_result = ESP_FAIL;
            tick(180000, true, true, -85, 3, 0);
            assert(votes == 0 && reselections == 1);
            assert(!root_recovery.has_parent_reselected && !root_recovery.has_executed);
            assert(!mesh_root_recovery_in_grace(&root_recovery, 180001));
            assert(root_recovery.severe_weak_active);
            assert(root_recovery.severe_weak_since_ms == 0);
            api_result = ESP_OK;
            tick(210000, true, true, -85, 3, 0);
            assert(votes == 0 && reselections == 2);
            assert(root_recovery.last_parent_reselection_ms == 210000);
        }
        static void priority(bool topology) {
            weak(0);
            if (topology) mesh_root_recovery_note_topology_change(&root_recovery, 120000, true);
            tick(180000, true, true, -85, topology ? 0 : 3, 0);
            assert(votes == 0 && reselections == 1);
            assert(!root_recovery.pending);
            /* Rejoins after reselection don't immediately trigger Level 1. */
            mesh_root_recovery_note_topology_change(&root_recovery, 210000, true);
            tick(210000, true, true, -90, 6, 2);
            assert(votes == 0 && reselections == 1);
        }
        static void wrap(uint32_t start) {
            weak(start);
            weak(start + 179999U);
            assert(reselections == 0);
            weak(start + 180000U);
            assert(reselections == 1);
            assert(mesh_root_recovery_in_grace(&root_recovery, start + 299999U));
            assert(!mesh_root_recovery_in_grace(&root_recovery, start + 300000U));
            weak(start + 210000U);
            weak(start + 1079999U);
            assert(reselections == 1);
            weak(start + 1080000U);
            assert(reselections == 2);
        }
        static void grace(bool level2) {
            /* MQTT has already timed out when recovery starts. */
            now_ms = 1000;
            check_mqtt_liveness();
            if (level2) weak(1000);
            else tick(1000, true, true, -80, 0, 0);
            tick(181000, true, true, level2 ? -85 : -80, 3, 0);
            assert(level2 ? reselections == 1 : votes == 1);
            now_ms = 300999;
            check_mqtt_liveness();
            assert(restarts == 0);
            now_ms = 301000;
            check_mqtt_liveness();
            assert(restarts == 1);
            /* An actual disconnect resets liveness; allow a fresh 180 seconds
             * after reconnection, even if recovery grace has already elapsed. */
            parent_connected = false;
            check_mqtt_liveness();
            assert(mqtt_liveness_since_us == 0);
            parent_connected = true;
            now_ms = 330000;
            check_mqtt_liveness();
            now_ms = 509999;
            check_mqtt_liveness();
            assert(restarts == 1);
            now_ms = 510000;
            check_mqtt_liveness();
            assert(restarts == 2);
        }
        static void execution_guards(void) {
            weak(0);
            parent_connected = false;
            weak(180000);
            parent_connected = true;
            is_rootless = true;
            weak(210000);
            is_rootless = false;
            now_ms = 240000;
            root = false;  /* Role changed after sampling. */
            try_root_recovery();
            assert(votes == 0 && reselections == 0);
            weak(270000);
            assert(reselections == 1);
        }
        static void mqtt_vote(void) {
            tick(0, true, true, -80, 0, 0);
            tick(30000, true, true, -80, 0, 2);
            assert(votes == 1 && reselections == 0);
        }
        int main(int argc, char **argv) {
            assert(argc == 2);
            mesh_root_recovery_init(&root_recovery);
            const char *scenario = argv[1];
            if (!strcmp(scenario, "sustained")) sustained();
            else if (strstr(scenario, "_reset")) reset_observation(scenario);
            else if (!strcmp(scenario, "invalid")) invalid();
            else if (!strcmp(scenario, "after_vote")) after_vote(false);
            else if (!strcmp(scenario, "child_after_vote")) after_vote(true);
            else if (!strcmp(scenario, "cooldown")) cooldown();
            else if (!strcmp(scenario, "cooldown_new_episode")) cooldown_then_new_episode();
            else if (!strcmp(scenario, "api_failure")) api_failure();
            else if (!strcmp(scenario, "priority_weak")) priority(false);
            else if (!strcmp(scenario, "priority_topology")) priority(true);
            else if (!strcmp(scenario, "wrap_duration")) wrap(UINT32_MAX - 100000U);
            else if (!strcmp(scenario, "wrap_cooldown")) wrap(UINT32_MAX - 250000U);
            else if (!strcmp(scenario, "level1_grace")) grace(false);
            else if (!strcmp(scenario, "level2_grace")) grace(true);
            else if (!strcmp(scenario, "guards")) execution_guards();
            else if (!strcmp(scenario, "mqtt_vote")) mqtt_vote();
            else assert(false);
            return 0;
        }
    '''
    directory = tmp_path_factory.mktemp("mesh-root-recovery")
    program = directory / "recovery.c"
    program.write_text(harness)
    executable = directory / "recovery"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
        str(program), str(SOURCE / "mesh_root_recovery.c"), str(SOURCE / "mesh_liveness.c"),
        "-o", str(executable),
    ], check=True)
    return executable


@pytest.mark.parametrize("scenario", [
    "sustained", "rssi_reset", "child_reset", "invalid_reset", "invalid",
    "after_vote", "child_after_vote", "cooldown", "cooldown_new_episode",
    "api_failure", "priority_weak", "priority_topology", "wrap_duration", "wrap_cooldown",
    "level1_grace", "level2_grace", "guards", "mqtt_vote",
])
def test_recovery_stages(recovery_binary, scenario):
    result = subprocess.run([str(recovery_binary), scenario], check=True, capture_output=True, text=True)
    if scenario == "api_failure":
        assert "root parent reselection failed: sustained_weak_root (ESP_FAIL)" in result.stdout
        assert "root parent reselection executed: sustained_weak_root" in result.stdout


def test_status_observes_link_then_recovers_before_liveness():
    source = (SOURCE / "mesh_network.c").read_text()
    status = source[source.index("static void publish_status"):source.index("static void ip_event_handler")]
    assert (status.index("mesh_root_recovery_observe_link(") < status.index("try_root_recovery();")
            < status.index("check_mqtt_liveness();"))
