"""Exercise the production startup configuration with IDF API stubs."""
import subprocess
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def configuration_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh-parent-switch")
    network = (SOURCE / "mesh_network.c").read_text()
    helper = network[network.index("static esp_err_t configure_parent_switch_parameters("):
                     network.index("static esp_err_t disable_root_conflicts(")]
    program = directory / "configuration.c"
    program.write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL (-1)
#define ESP_ERR_MESH_ARGUMENT 0x4008
#define TAG "test"
#define ESP_LOGI(tag, ...) do { (void)(tag); printf(__VA_ARGS__); putchar('\n'); } while (0)
#define ESP_LOGE ESP_LOGI
/* ESP-IDF 6.0.1 esp_mesh_internal.h layout; the firmware build checks the real header. */
typedef struct {
    int duration_ms, cnx_rssi, select_rssi, switch_rssi, backoff_rssi;
} mesh_switch_parent_t;
static mesh_switch_parent_t original, runtime;
static const char *scenario;
static int gets, sets, mismatch;
static bool organized;
static const char *esp_err_to_name(esp_err_t err) {
    assert(err == ESP_ERR_MESH_ARGUMENT);
    return "ESP_ERR_MESH_ARGUMENT";
}
static esp_err_t esp_mesh_get_switch_parent_paras(mesh_switch_parent_t *out) {
    ++gets;
    assert(gets <= 2 && sets == gets - 1);
    if (!strcmp(scenario, "get_failure") ||
        (!strcmp(scenario, "readback_failure") && gets == 2)) return ESP_ERR_MESH_ARGUMENT;
    *out = runtime;
    if (!strcmp(scenario, "mismatch") && gets == 2) {
        switch (mismatch) {
        case 0: ++out->duration_ms; break;
        case 1: ++out->cnx_rssi; break;
        case 2: ++out->select_rssi; break;
        case 3: ++out->switch_rssi; break;
        case 4: ++out->backoff_rssi; break;
        default: assert(false);
        }
    }
    return ESP_OK;
}
static esp_err_t esp_mesh_set_switch_parent_paras(mesh_switch_parent_t *in) {
    assert(gets == 1 && ++sets == 1);
    assert(in->cnx_rssi == -78 && in->backoff_rssi == -78);
    assert(in->duration_ms == original.duration_ms);
    assert(in->select_rssi == original.select_rssi && in->switch_rssi == original.switch_rssi);
    if (!strcmp(scenario, "set_failure")) return ESP_ERR_MESH_ARGUMENT;
    runtime = *in;
    return ESP_OK;
}
static bool esp_mesh_get_self_organized(void) {
    assert(gets == 2 && sets == 1);
    return organized;
}
''' + helper + r'''
int main(int argc, char **argv) {
    assert(argc == 3);
    scenario = argv[1];
    mismatch = atoi(argv[2]);
    organized = mismatch == 0;
    original = organized ? (mesh_switch_parent_t){6000, -115, -68, -86, -92} :
                           (mesh_switch_parent_t){43210, -66, -74, -89, -70};
    runtime = original;
    esp_err_t err = configure_parent_switch_parameters();
    if (!strcmp(scenario, "get_failure")) {
        assert(err == ESP_ERR_MESH_ARGUMENT && gets == 1 && sets == 0);
    } else if (!strcmp(scenario, "set_failure")) {
        assert(err == ESP_ERR_MESH_ARGUMENT && gets == 1 && sets == 1);
    } else if (!strcmp(scenario, "readback_failure")) {
        assert(err == ESP_ERR_MESH_ARGUMENT && gets == 2 && sets == 1);
    } else {
        assert(gets == 2 && sets == 1);
        assert(err == (!strcmp(scenario, "mismatch") ? ESP_FAIL : ESP_OK));
    }
}
''')
    binary = directory / "configuration"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(program),
                    "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("profile,values", [
    (0, "self_organized=1 duration_ms=6000 cnx_rssi=-78 select_rssi=-68 switch_rssi=-86 backoff_rssi=-78"),
    (1, "self_organized=0 duration_ms=43210 cnx_rssi=-78 select_rssi=-74 switch_rssi=-89 backoff_rssi=-78"),
])
def test_only_two_thresholds_change_and_actual_runtime_is_logged(configuration_binary, profile, values):
    result = subprocess.run([str(configuration_binary), "success", str(profile)],
                            check=True, capture_output=True, text=True)
    assert f"Mesh parent switching (read-back): {values}" in result.stdout


@pytest.mark.parametrize("scenario,message", [
    ("get_failure", "Could not get Mesh parent switching parameters"),
    ("set_failure", "Could not set Mesh parent switching parameters"),
    ("readback_failure", "Mesh parent switching read-back failed"),
])
def test_api_failures_stop_configuration(configuration_binary, scenario, message):
    result = subprocess.run([str(configuration_binary), scenario, "0"],
                            check=True, capture_output=True, text=True)
    assert f"{message}: ESP_ERR_MESH_ARGUMENT" in result.stdout
    assert "Mesh parent switching (read-back):" not in result.stdout


@pytest.mark.parametrize("field", range(5))
def test_readback_mismatch_is_reported_as_failure(configuration_binary, field):
    result = subprocess.run([str(configuration_binary), "mismatch", str(field)],
                            check=True, capture_output=True, text=True)
    assert "Mesh parent switching (read-back):" in result.stdout
    assert "Mesh parent switching read-back does not match requested parameters" in result.stdout


def test_configuration_failure_prevents_mesh_start():
    network = (SOURCE / "mesh_network.c").read_text()
    start = network[network.index("esp_err_t mesh_network_start_prepared(void)"):]
    call = "err = configure_parent_switch_parameters();\n    if (err != ESP_OK) return err;"
    assert call in start
    assert start.index("esp_mesh_init();") < start.index("esp_mesh_set_config(&config);")
    assert start.index("esp_mesh_set_config(&config);") < start.index(call) < start.index("esp_mesh_start();")
    assert network.count("configure_parent_switch_parameters();") == 1
