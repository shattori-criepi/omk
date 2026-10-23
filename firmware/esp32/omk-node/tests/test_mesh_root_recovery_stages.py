"""Run retained root voting and the real API dispatch/liveness path with host stubs."""
import subprocess
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def recovery_binary(tmp_path_factory):
    source = (SOURCE / "mesh_network.c").read_text()
    dispatch = source[source.index("static void try_root_recovery(void)"):
                      source.index("void mesh_network_log_diagnostics")]
    harness = r'''
        #include <assert.h>
        #include <stdbool.h>
        #include <stdint.h>
        #include <stdio.h>
        #include <string.h>
        #include "mesh_root_recovery.h"
        #include "mesh_recovery.h"
        #define portENTER_CRITICAL(lock) ((void)0)
        #define portEXIT_CRITICAL(lock) ((void)0)

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
        static omk_mesh_recovery_t recovery;
        static unsigned votes, restarts;
        static esp_err_t api_result = ESP_OK;
        static uint32_t uptime_milliseconds(void) { return now_ms; }
        static bool esp_mesh_is_root(void) { return root; }
        static const char *esp_err_to_name(esp_err_t err) {
            assert(err == ESP_FAIL);
            return "ESP_FAIL";
        }
        static esp_err_t esp_mesh_waive_root(const void *vote, int reason) {
            assert(root && vote == NULL && reason == MESH_VOTE_REASON_ROOT_INITIATED);
            ++votes;
            return api_result;
        }
    ''' + dispatch + r'''
        static void check_mqtt_liveness(void) {
            omk_mesh_recovery_input_t in = {
                .started=started, .parent_connected=parent_connected, .is_root=root,
                .rootless=is_rootless, .has_ip=current_ip.addr != 0,
                .mqtt_started=true, .mqtt_connected=mqtt_connected,
            };
            omk_mesh_recovery_action_t action = mesh_recovery_choose(&recovery, now_ms, &in, &root_recovery);
            assert(action <= OMK_RECOVERY_ROOT_VOTE);
        }
        static void tick(uint32_t now, bool is_root, bool valid, int rssi,
                         uint32_t parent_count, uint32_t mqtt_count) {
            now_ms = now;
            root = is_root;
            unsigned before = votes;
            mesh_root_recovery_observe_link(&root_recovery, now, root, valid, rssi,
                                            parent_count, mqtt_count);
            try_root_recovery();
            assert(votes <= before + 1);
        }
        static void weak(uint32_t now) { tick(now, true, true, -85, 0, 0); }
        static void sustained(void) {
            for (uint32_t t = 0; t <= 7200000; t += 30000) weak(t);
            assert(votes == 0);
        }
        static void invalid(void) {
            for (uint32_t t = 0; t <= 360000; t += 30000)
                tick(t, true, false, -90, t / 10000, t / 15000);
            assert(votes == 0);
        }
        static void after_vote(bool becomes_child) {
            tick(0, true, true, -89, 0, 0);
            tick(30000, true, true, -89, 3, 0);
            assert(votes == 1);
            for (uint32_t t = 60000; t <= 1800000; t += 30000)
                tick(t, !becomes_child, true, -89, 3, 0);
            assert(votes == 1); /* No RSSI-only second stage, even after cooldown. */
        }
        static void cooldown(uint32_t start) {
            tick(start, true, true, -85, 0, 0);
            tick(start + 30000U, true, true, -85, 3, 0);
            assert(votes == 1);
            tick(start + 929999U, true, true, -85, 6, 0);
            assert(votes == 1);
            tick(start + 930000U, true, true, -85, 9, 0);
            assert(votes == 2);
        }
        static void api_failure(void) {
            weak(0);
            api_result = ESP_FAIL;
            tick(30000, true, true, -85, 3, 0);
            assert(votes == 1 && root_recovery.pending);
            assert(!mesh_root_recovery_in_grace(&root_recovery, 30001));
            assert(!root_recovery.cooldown_active);
            tick(60000, true, true, -85, 3, 0);
            assert(votes == 2 && root_recovery.pending);
            api_result = ESP_OK;
            tick(90000, true, true, -85, 3, 0);
            assert(votes == 3 && !root_recovery.pending);
            assert(mesh_root_recovery_in_grace(&root_recovery, 90001));
        }
        static void grace(void) {
            /* MQTT has already timed out when recovery starts. */
            now_ms = 1000;
            check_mqtt_liveness();
            tick(1000, true, true, -80, 0, 0);
            tick(181000, true, true, -80, 3, 0);
            assert(votes == 1);
            now_ms = 300999;
            check_mqtt_liveness();
            assert(restarts == 0);
            now_ms = 301000;
            check_mqtt_liveness();
            assert(restarts == 0);
            /* An actual disconnect resets liveness; allow a fresh 180 seconds
             * after reconnection, even if recovery grace has already elapsed. */
            parent_connected = false;
            check_mqtt_liveness();
            assert(recovery.loss_active);
            parent_connected = true;
            now_ms = 330000;
            check_mqtt_liveness();
            now_ms = 509999;
            check_mqtt_liveness();
            assert(restarts == 0);
            now_ms = 510000;
            check_mqtt_liveness();
            assert(restarts == 0 && recovery.stage == OMK_RECOVERY_MQTT_WAIT);
        }
        static void execution_guards(void) {
            weak(0);
            parent_connected = false;
            tick(180000, true, true, -85, 3, 0);
            parent_connected = true;
            is_rootless = true;
            tick(210000, true, true, -85, 3, 0);
            is_rootless = false;
            now_ms = 240000;
            root = false;  /* Role changed after sampling. */
            try_root_recovery();
            assert(votes == 0);
            tick(270000, true, true, -85, 3, 0);
            assert(votes == 1);
        }
        static void mqtt_vote(void) {
            tick(0, true, true, -80, 0, 0);
            tick(30000, true, true, -80, 0, 2);
            assert(votes == 1);
        }
        int main(int argc, char **argv) {
            assert(argc == 2);
            mesh_root_recovery_init(&root_recovery);
            mesh_recovery_init(&recovery, 0);
            recovery.jitter_ms = 0;
            const char *scenario = argv[1];
            if (!strcmp(scenario, "sustained")) sustained();
            else if (!strcmp(scenario, "invalid")) invalid();
            else if (!strcmp(scenario, "after_vote")) after_vote(false);
            else if (!strcmp(scenario, "child_after_vote")) after_vote(true);
            else if (!strcmp(scenario, "cooldown")) cooldown(0);
            else if (!strcmp(scenario, "wrap_cooldown")) cooldown(UINT32_MAX - 250000U);
            else if (!strcmp(scenario, "api_failure")) api_failure();
            else if (!strcmp(scenario, "grace")) grace();
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
        str(program), str(SOURCE / "mesh_root_recovery.c"), str(SOURCE / "mesh_liveness.c"), str(SOURCE / "mesh_recovery.c"),
        "-o", str(executable),
    ], check=True)
    return executable


@pytest.mark.parametrize("scenario", [
    "sustained", "invalid", "after_vote", "child_after_vote", "cooldown",
    "api_failure", "wrap_cooldown", "grace", "guards", "mqtt_vote",
])
def test_root_vote_dispatch(recovery_binary, scenario):
    result = subprocess.run([str(recovery_binary), scenario], check=True, capture_output=True, text=True)
    if scenario == "api_failure":
        assert "root reelection deferred: root_link_unhealthy (ESP_FAIL)" in result.stdout
        assert "root reelection executed: root_link_unhealthy (level=1)" in result.stdout


def test_status_observes_link_then_recovers_before_liveness():
    source = (SOURCE / "mesh_network.c").read_text()
    status = source[source.index("static void publish_status"):source.index("static void ip_event_handler")]
    assert status.index("mesh_root_recovery_observe_link(") < status.index("publish_recovery_status(node_id);")
    assert "try_recovery();" not in status
    monitor = source[source.index("static void recovery_task"):source.index("static void publish_recovery_status")]
    assert "try_recovery();" in monitor
    assert "vTaskDelay(pdMS_TO_TICKS(1000))" in monitor
