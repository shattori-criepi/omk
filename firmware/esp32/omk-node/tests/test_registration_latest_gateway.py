"""Replay production Node output into the actual Gateway registration methods.

Extract only the Gateway methods under test so this firmware suite does not
require the unrelated FastAPI/BLE runtime. Registry persistence is unmodified;
MQTT transport and BLE discovery are adapters, not registration logic copies.
"""
import ast
import importlib.util
import json
import logging
from pathlib import Path
import subprocess
from threading import RLock
from types import SimpleNamespace

import pytest

from test_sen66_delivery import delivery_binary  # noqa: F401: shared parametrized fixture


GATEWAY = Path(__file__).resolve().parents[4] / "services/ble-sensor-manager/src/omk_ble"
NODE = "9490b46aee0d"


@pytest.fixture
def manager(tmp_path):
    tree = ast.parse((GATEWAY / "service.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BleManager")
    methods = {"_node_id_valid", "_logical_id_valid", "_logical_id_owner",
               "request_node_registration", "remove_node_registration", "handle_node_mqtt"}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    assert {n.name for n in cls.body} == methods
    future = ast.parse("from __future__ import annotations").body
    module = ast.Module(body=future + [cls], type_ignores=[])
    namespace = {"json": json, "LOGGER": logging.getLogger(__name__),
                 "mqtt": SimpleNamespace(MQTT_ERR_SUCCESS=0),
                 "now_iso": lambda: "2026-10-09T00:00:00+00:00"}
    exec(compile(module, str(GATEWAY / "service.py"), "exec"), namespace)
    spec = importlib.util.spec_from_file_location("tested_node_registry", GATEWAY / "node_registry.py")
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    m = namespace["BleManager"]()
    m.node_registry = registry.NodeRegistry(tmp_path / "nodes.json")
    m._node_registration_lock = RLock()
    m.node_list = lambda: list(m.node_registry.list().values())
    m.sent = []

    def publish(topic, payload, **kwargs):
        m.sent.append((topic, json.loads(payload), kwargs))
        return SimpleNamespace(rc=0)

    m._mqtt = SimpleNamespace(publish=publish)
    return m


@pytest.mark.parametrize("scenario,initial,final", [
    ("latest_burst_register", None, "latest-id"),
    ("latest_burst_change", "sen66-002", "latest-id"),
    ("latest_burst_clear", "sen66-002", None),
    ("inflight_register_register", "sen66-002", "latest-id"),
    ("inflight_register_clear", "sen66-002", None),
    ("inflight_clear_register", "sen66-002", "latest-id"),
    ("inflight_clear_clear", "sen66-002", None),
])
def test_latest_request_converges_with_gateway(manager, delivery_binary, scenario, initial, final):
    manager.node_registry.update(NODE, registration_state="registered" if initial else "provisioned",
                                 logical_id=initial)
    if scenario.startswith("inflight_"):
        if scenario.startswith("inflight_clear_"):
            manager.remove_node_registration(NODE)
        else:
            manager.request_node_registration(NODE, "inflight-id")
    for i in range(1, 13):
        desired = final if i == 12 else None if i in (4, 10) else f"superseded-{i}"
        if desired is None:
            manager.remove_node_registration(NODE)
        else:
            manager.request_node_registration(NODE, desired)
    assert len(manager.sent) >= 12
    for _, body, options in manager.sent:
        assert options == {"qos": 1, "retain": body["logical_id"] is None}
    current = manager.node_registry.list()[NODE]
    assert current.get("requested_logical_id") == final

    # The real Node harness emits its publication order, including any old
    # in-flight ACK/status and all retained deletions. No request is resent.
    result = subprocess.run([str(delivery_binary), scenario, "emit"], capture_output=True,
                            text=True, timeout=15, check=True)
    for line in result.stdout.splitlines():
        topic, payload = line.split("\t", 1)
        manager.handle_node_mqtt(topic, payload.encode())
    current = manager.node_registry.list()[NODE]
    assert current.get("logical_id") == final
    assert current.get("requested_logical_id") is None
    assert current["registration_state"] == ("registered" if final else "provisioned")
    if final:
        assert current["request_state"] == "registered"
        assert not current["registration_revoked"]
    else:
        assert current["registration_revoked"]
