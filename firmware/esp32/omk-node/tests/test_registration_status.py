"""Execute the firmware's actual status builder on the host with NVS/MQTT stubs."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).parents[1] / "src"


@pytest.fixture(scope="module")
def status_binary(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("Host C compiler required for firmware status payload tests")
    source = (SOURCE / "mqtt_registration.c").read_text()
    validation_start = source.index("static bool logical_id_is_valid(const char *logical_id) {")
    validation = source[validation_start:source.index("/* ``omk/", validation_start)]
    persisted = source[source.index("static bool registration_is_persisted"):source.index("static void publish_registration_status")]
    status = source[source.index("static void publish_registration_status"):validation_start]
    constants = "\n".join(re.search(r"^#define " + name + r" .+$", text, re.M).group(0) for name, text in (
        ("OMK_REGISTRATION_PAYLOAD_SIZE", source),
        ("OMK_NODE_LOGICAL_ID_MAX_LENGTH", (SOURCE / "node_registration.h").read_text()),
    ))
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ESP_OK 0
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
#define OMK_NODE_PROVISIONING_STATE_REGISTERED 2
#define OMK_NODE_PROTOCOL_VERSION 1
#define OMK_NODE_CAPABILITIES 3
    ''' + constants + r'''
typedef int esp_err_t;
static int client;
static char client_id[] = "omk-node-020000000001";
static char registration_topic[] = "omk/node/020000000001/registration/status";
static char registration_payload[OMK_REGISTRATION_PAYLOAD_SIZE];
static bool sen66_connected, sen66_diagnostics_available;
static uint32_t sen66_recovery_count = UINT32_MAX, sen66_measurement_timeout_count = UINT32_MAX;
static bool saved_registered, read_failure;
static const char *saved_id;
static int id_reads;
static esp_err_t node_registration_get_provisioning_state(bool wifi, uint8_t *state) {
    (void)wifi;
    *state = saved_registered ? OMK_NODE_PROVISIONING_STATE_REGISTERED : 1;
    return ESP_OK;
}
static esp_err_t node_registration_get_logical_id(char *value, size_t size) {
    ++id_reads;
    if (read_failure || strlen(saved_id) >= size) return -1;
    strcpy(value, saved_id);
    return ESP_OK;
}
static int esp_mqtt_client_publish(int handle, const char *topic, const char *payload, int length, int qos, int retain) {
    (void)handle;
    assert(strcmp(topic, registration_topic) == 0);
    assert(length == 0 && qos == 1 && retain == 1);
    puts(payload);
    return 1;
}
    ''' + validation + persisted + status + r'''
int main(int argc, char **argv) {
    assert(argc == 6);
    saved_registered = atoi(argv[1]);
    saved_id = argv[2];
    read_failure = atoi(argv[3]);
    sen66_diagnostics_available = atoi(argv[4]);
    sen66_connected = atoi(argv[5]);
    publish_registration_status();
    fprintf(stderr, "%d", id_reads);
    return 0;
}
'''
    root = tmp_path_factory.mktemp("registration-status")
    (root / "status.c").write_text(harness)
    executable = root / "status"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(root / "status.c"), "-o", str(executable)], check=True, capture_output=True, text=True)
    return executable


@pytest.mark.parametrize("registered,logical_id", [(True, "sen66-001"), (True, "A" * 48),
                                                     (False, "stale-id"), (False, 'invalid/"id')])
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("connected", [False, True])
def test_status_payload_comes_from_persisted_identity(status_binary, registered, logical_id, diagnostics, connected):
    result = subprocess.run([str(status_binary), str(int(registered)), logical_id, "0", str(int(diagnostics)), str(int(connected))],
                            check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload["protocol_version"] == 1
    assert payload["node_id"] == "020000000001"
    assert payload["registration_state"] == ("registered" if registered else "provisioned")
    assert payload["capabilities"] == 3
    assert payload["connected_sensors"] == (["sen66"] if connected else [])
    if registered:
        assert payload["logical_id"] == logical_id
    else:
        assert "logical_id" not in payload
    assert result.stderr == ("1" if registered else "0")
    if diagnostics:
        assert payload["sen66_rc"] == payload["sen66_to"] == 4294967295
    else:
        assert "sen66_rc" not in payload and "sen66_to" not in payload


@pytest.mark.parametrize("logical_id,read_failure", [("", 0), ("a" * 49, 0), ("invalid/id", 0),
                                                       ('bad"id', 0), ("センサ", 0), ("sen66-001", 1)])
def test_registered_status_does_not_publish_invalid_or_unreadable_nvs_identity(status_binary, logical_id, read_failure):
    result = subprocess.run([str(status_binary), "1", logical_id, str(read_failure), "0", "0"],
                            check=True, capture_output=True, text=True)
    assert result.stdout == ""


def test_connect_synchronizes_status_without_synthesizing_an_ack():
    source = (SOURCE / "mqtt_registration.c").read_text()
    connected = source[source.index("case MQTT_EVENT_CONNECTED:"):source.index("case MQTT_EVENT_DATA:")]
    assert "publish_registration_status();" in connected
    assert "publish_registration_ack" not in connected
    configured = source[source.index("esp_err_t save_err = node_registration_save"):source.index("static void mqtt_event_handler")]
    assert configured.index("publish_registration_ack(logical_id->valuestring);") < configured.index("publish_registration_status();")
