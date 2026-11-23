"""Exercise the production Root topic formatter, then the Gateway public gate."""
import json
import os
from pathlib import Path
import subprocess

import pytest

BASE = Path(__file__).resolve().parents[1]
CJSON_LIMIT = next(line.split("=", 1)[1] for line in (BASE / "sdkconfig.atom-s3-lite").read_text().splitlines() if line.startswith("CONFIG_CJSON_NESTING_LIMIT="))


@pytest.fixture(scope="module")
def root_topic(tmp_path_factory):
    directory = tmp_path_factory.mktemp("root-publication")
    source = (BASE / "src/mesh_transport.c").read_text()
    # Compile the actual pure formatter and its JSON validators, without the
    # unrelated RTOS/network workers. No duplicate routing implementation.
    validators = source[source.index("static bool valid_uint("):source.index("static bool valid_config(")]
    start = source.index("static bool valid_boot_id(")
    formatter = (source[start:source.index("static bool parse_status_identity(", start)] +
                 source[source.index("static bool publication_topic("):source.index("static origin_route_t *find_origin(")])
    start = source.index("typedef struct {\n    uint64_t uptime_s;")
    identity = source[start:source.index("} status_identity_t;", start) + len("} status_identity_t;")]
    harness = directory / "root_topic.c"
    harness.write_text('''#include <stdio.h>
#include <string.h>
#include <math.h>
#include "cJSON.h"
#include "mesh_wire.h"
#include "ble_scan_trial.h"
#define MQTT_TOPIC_SIZE 96
''' + identity + validators + formatter + '''
int main(int argc, char **argv) {
    if (argc != 2 || strlen(argv[1]) > MESH_TRANSPORT_PAYLOAD_MAX) return 2;
    mesh_wire_message_t message = {.kind = MESH_MESSAGE_SEN66};
    strcpy(message.node_id, "020000000001");
    strcpy(message.payload, argv[1]);
    message.length = strlen(argv[1]);
    char topic[MQTT_TOPIC_SIZE]; uint32_t revision; status_identity_t identity;
    if (!publication_topic(&message, topic, &revision, &identity)) return 1;
    puts(topic); return 0;
}
''')
    cjson = BASE / "managed_components/espressif__cjson/cJSON"
    if not cjson.exists():
        cjson = Path(os.environ["OMK_TEST_CJSON_DIR"])
    binary = directory / "root_topic"
    subprocess.run(["cc", "-std=c11", "-DCJSON_NESTING_LIMIT=" + CJSON_LIMIT, "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
                    "-I", str(BASE / "src"), "-I", str(cjson), str(harness),
                    str(BASE / "src/mesh_wire.c"), str(cjson / "cJSON.c"), "-lm", "-o", str(binary)], check=True)
    return binary


def test_root_sen66_only_reaches_public_topic_after_gateway_authorization(root_topic, tmp_path):
    from types import SimpleNamespace
    from omk_ble.node_registry import NodeRegistry
    from omk_ble.registry import SensorRegistry
    from omk_ble.service import BleManager

    value = {"protocol_version": 2, "node_id": "020000000001", "revision": 7,
             "seq": 42, "boot_id": "0000000000000001", "device_id": "sen66-001",
             "temperature_celsius": 23.5, "co2_ppm": 600}
    payload = json.dumps(value)
    topic = subprocess.check_output([str(root_topic), payload], text=True).strip()
    assert topic == "omk-relay/020000000001/sen66/raw"
    publications = []
    publisher = SimpleNamespace(is_connected=lambda: True,
        publish=lambda topic, payload, **flags: publications.append((topic, payload, flags)))
    registry = NodeRegistry(tmp_path / "nodes.json")
    registry.update(value["node_id"], revision=8, registration_revoked=True)
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher, node_registry=registry)
    manager.handle_sen66_mqtt(topic, payload.encode())
    assert publications == []  # Delayed Root outbox data cannot reach any omk/# consumer.
    registry.update(value["node_id"], registration_state="registered", logical_id="sen66-001",
                    revision=7, registration_revoked=False)
    manager.handle_sen66_mqtt(topic, payload.encode())
    assert publications[0] == ("omk/sen66-001/sen66", payload.encode(), {"qos": 0, "retain": False})
