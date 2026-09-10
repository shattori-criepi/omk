"""Host-compile the unchanged relay C source with fake clock/MQTT (no ESP32 I/O)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from omk_ble.switchbot import METER_SERVICE_UUID, SWITCHBOT_COMPANY_ID, decode


def test_real_relay_fragment_join_and_gap_expiry_preserve_model_evidence(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("host C compiler unavailable")
    root = Path(__file__).resolve().parents[3]
    source = root / "firmware/esp32/omk-node/src/switchbot_relay.c"
    # Copy verbatim so quoted includes resolve against isolated fake headers.
    shutil.copyfile(source, tmp_path / "relay.c")
    (tmp_path / "switchbot_relay.h").write_text("#include <stdint.h>\n#include <stddef.h>\n")
    (tmp_path / "esp_log.h").write_text('#define ESP_LOGW(...) ((void)0)\n')
    (tmp_path / "esp_timer.h").write_text('#include <stdint.h>\nint64_t esp_timer_get_time(void);\n')
    (tmp_path / "mqtt_registration.h").write_text('''
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE 1
const char *esp_err_to_name(int);
int mqtt_registration_publish_ble_relay(const char *, const char *, int,
                                        const uint8_t *, size_t, const uint8_t *, size_t);
''')
    (tmp_path / "node_identity.h").write_text('''
#define OMK_NODE_ID_HEX_LENGTH 12
int node_identity_get_id_hex(char *, size_t);
''')
    (tmp_path / "harness.c").write_text(r'''
#include <stdio.h>
#include <string.h>
#include "relay.c"
static int64_t fake_time;
int64_t esp_timer_get_time(void) { return fake_time; }
const char *esp_err_to_name(int err) { return "fake"; }
int node_identity_get_id_hex(char *out, size_t length) {
    snprintf(out, length, "020000000041"); return ESP_OK;
}
int mqtt_registration_publish_ble_relay(const char *node, const char *address, int rssi,
                                        const uint8_t *md, size_t ml, const uint8_t *sd, size_t sl) {
    printf("%s ", address);
    for (size_t i=0; i<ml; i++) printf("%02x", md[i]);
    printf(" ");
    for (size_t i=0; i<sl; i++) printf("%02x", sd[i]);
    printf("\n"); return ESP_OK;
}
int main(void) {
    uint8_t address[] = {2,0,0,0,0,64};
    uint8_t md[] = {2,0,0,0,0,64,0x7f,0x80,0x10,0x96,0,0};
    uint8_t service[] = {0x6a,0,100};
    switchbot_relay_handle_observation(address, -50, md, sizeof(md), NULL, 0);
    fake_time = 200000;
    switchbot_relay_handle_observation(address, -50, NULL, 0, service, sizeof(service));
    fake_time = 300000;
    switchbot_relay_handle_observation(address, -50, md, sizeof(md), NULL, 0);
    fake_time = 11000000; /* Gap expiry must discard the old service field. */
    switchbot_relay_handle_observation(address, -50, md, sizeof(md), NULL, 0);
    return 0;
}
''')
    binary = tmp_path / "relay-test"
    subprocess.run([compiler, "-std=c11", str(tmp_path / "harness.c"), "-o", str(binary)], check=True, capture_output=True)
    output = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout
    decoded = []
    for line in output.splitlines():
        address, md, sd = line.split(" ")
        decoded.append(decode(address, -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex(md)},
                              {METER_SERVICE_UUID: bytes.fromhex(sd)} if sd else {}, "now"))
    assert [item.model for item in decoded] == ["unknown_switchbot", "plug_sensor", "unknown_switchbot"]
    assert decoded[1].values == {"switch_state": 1, "power_w": 0.0}
    assert len({item.device_key for item in decoded}) == 1
