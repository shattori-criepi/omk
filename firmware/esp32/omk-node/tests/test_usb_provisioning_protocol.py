"""Execute production USB command handlers with real cJSON and fake storage.

Requires a host C compiler and PlatformIO's ESP-IDF cJSON sources (or
OMK_TEST_CJSON_DIR). No firmware upload, serial device or real credential is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).parents[1] / "src/usb_provisioning.c"
NODE_ID = "020000000001"  # Synthetic identity, unrelated to any physical Node.


@pytest.fixture(scope="module")
def handler(tmp_path_factory):
    compiler = shutil.which("cc")
    configured = os.environ.get("OMK_TEST_CJSON_DIR")
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    candidates = [Path(configured)] if configured else sorted(core.glob("packages/framework-espidf*/components/json/cJSON"))
    cjson = next((path for path in candidates if (path / "cJSON.c").is_file()), None)
    if compiler is None or cjson is None:
        pytest.skip("host cc and ESP-IDF cJSON required; set OMK_TEST_CJSON_DIR")
    directory = tmp_path_factory.mktemp("usb-protocol")
    source = SOURCE.read_text()
    # Compile the actual command handlers, replacing only platform I/O/storage.
    handlers = source[source.index("#define USB_PROVISIONING_PROTOCOL_VERSION"):source.index("static void usb_provisioning_task")]
    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
typedef int esp_err_t;
#define ESP_OK 0
#define pdMS_TO_TICKS(x) (x)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
typedef struct { struct { uint8_t ssid[32]; uint8_t password[64]; } sta; } wifi_config_t;
#define NODE_PROVISIONING_POP_LENGTH 32
#define NODE_NVS_NAMESPACE "omk"
#define NODE_NVS_PROVISIONING_POP_KEY "prov_pop"
#define NVS_READONLY 0
#define OMK_NODE_PROVISIONING_STATE_REGISTERED 2
#define OMK_NODE_PROVISIONING_STATE_UNREGISTERED 0
typedef int nvs_handle_t;
static int nvs_open(const char *name, int mode, nvs_handle_t *handle) {
    (void)name; (void)mode; *handle = 1; return ESP_OK;
}
static int nvs_get_blob(nvs_handle_t nvs, const char *key, void *value, size_t *size) {
    (void)nvs; (void)key; memset(value, 0xab, *size); return ESP_OK;
}
static void nvs_close(nvs_handle_t nvs) { (void)nvs; }
static bool saved_wifi;
static uint8_t saved_state;
static int node_registration_get_provisioning_state(bool wifi, uint8_t *state) {
    (void)wifi; *state = saved_state; return ESP_OK;
}
static unsigned saves, clears;
static int usb_serial_jtag_write_bytes(const char *value, size_t length, int timeout) {
    (void)timeout; return (int)fwrite(value, 1, length, stdout);
}
static esp_err_t wifi_station_has_saved_credentials(bool *configured) { *configured = saved_wifi; return ESP_OK; }
static esp_err_t wifi_station_clear_saved_credentials(void) { ++clears; return ESP_OK; }
static esp_err_t wifi_station_save_credentials(const uint8_t *ssid, size_t sl, const uint8_t *psk, size_t pl) {
    (void)ssid; (void)sl; (void)psk; (void)pl; ++saves; return ESP_OK;
}
'''
    harness += handlers
    harness += r'''
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    saved_wifi = atoi(argv[2]);
    saved_state = atoi(argv[3]);
    provision_node_id = UINT64_C(0x020000000001);
    handle_line(argv[1]);
    printf("{\"saves\":%u,\"clears\":%u,\"reboot\":%s}\n", saves, clears, reboot_scheduled ? "true" : "false");
    return 0;
}
'''
    path = directory / "handler.c"
    path.write_text(harness)
    binary = directory / "handler"
    subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable",
                    "-I", str(cjson), str(path), str(cjson / "cJSON.c"), "-lm", "-o", str(binary)], check=True, capture_output=True, text=True)
    def run(request, wifi=False, registered=False):
        result = subprocess.run([str(binary), json.dumps(request), str(int(wifi)), str(2 if registered else 0)], check=True, capture_output=True, text=True)
        assert "synthetic-secret" not in result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]
    return run


@pytest.mark.parametrize("command", ["set_wifi", "clear_wifi"])
@pytest.mark.parametrize("expected", [NODE_ID, "020000000002", None, "", 123, "020000000001extra"])
def test_expected_identity_precedes_every_storage_change(handler, command, expected):
    request = {"protocol_version": 2, "command": command}
    if command == "set_wifi": request.update(ssid="OMK-TEST", password="synthetic-secret")
    if expected is not None: request["expected_node_id"] = expected
    response, state = handler(request)
    if expected == NODE_ID:
        assert response == {"status": "accepted", "protocol_version": 2, "node_id": NODE_ID}
        assert state == {"saves": int(command == "set_wifi"), "clears": int(command == "clear_wifi"), "reboot": True}
    else:
        assert response["status"] == "node_identity_changed"
        assert state == {"saves": 0, "clears": 0, "reboot": False}


@pytest.mark.parametrize("command", ["set_wifi", "clear_wifi"])
def test_legacy_host_cannot_mutate_new_firmware(handler, command):
    response, state = handler({"protocol_version": 1, "command": command,
                               "ssid": "OMK-TEST", "password": "synthetic-secret"})
    assert response["status"] == "invalid_request"
    assert state == {"saves": 0, "clears": 0, "reboot": False}


def test_identify_is_read_only_and_does_not_require_expected_identity(handler):
    response, state = handler({"protocol_version": 2, "command": "identify"})
    assert response == {"status": "ok", "protocol_version": 2, "node_id": NODE_ID, "wifi_configured": False}
    assert state == {"saves": 0, "clears": 0, "reboot": False}


def test_legacy_identify_is_read_only_and_advertises_v2(handler):
    response, state = handler({"protocol_version": 1, "command": "identify"})
    assert response["protocol_version"] == 2 and response["status"] == "ok"
    assert state == {"saves": 0, "clears": 0, "reboot": False}


@pytest.mark.parametrize("secret,expected", [("ab" * 32, "accepted"), ("cd" * 32, "storage_error"), (None, "storage_error")])
def test_reinitialize_verification_proves_pop_without_mutation_or_reboot(handler, secret, expected):
    response, state = handler(dict(protocol_version=2, command="verify_reinitialize",
                                   expected_node_id=NODE_ID, provisioning_secret=secret))
    assert response["status"] == expected
    assert state == {"saves": 0, "clears": 0, "reboot": False}
    assert secret is None or secret not in json.dumps(response)


@pytest.mark.parametrize("wifi,registered", [(True, False), (False, True), (True, True)])
def test_reinitialize_verification_rejects_residual_wifi_or_registration(handler, wifi, registered):
    response, state = handler(dict(protocol_version=2, command="verify_reinitialize",
                                   expected_node_id=NODE_ID, provisioning_secret="ab" * 32), wifi, registered)
    assert response["status"] == "storage_error"
    assert state == {"saves": 0, "clears": 0, "reboot": False}


def test_reinitialize_verification_rejects_wrong_physical_node(handler):
    response, _ = handler(dict(protocol_version=2, command="verify_reinitialize",
                               expected_node_id="020000000002", provisioning_secret="ab" * 32))
    assert response["status"] == "node_identity_changed"
