"""Saved Native Gateway endpoint retry contract; no broker or network access."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from omk_ble.node_registry import NodeRegistry
from omk_ble.registry import SensorRegistry
from omk_ble.service import BleManager

NODE = "020000000001"


@pytest.fixture
def control(tmp_path):
    clock, sent, published = [0.0], [], [True]

    def publish(topic, payload, **flags):
        sent.append((topic, json.loads(payload) if payload else None, flags))
        return SimpleNamespace(rc=0, is_published=lambda: published[0])

    publisher = SimpleNamespace(is_connected=lambda: True, publish=publish)
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher,
                         now_provider=lambda: datetime(2026, 11, 13, tzinfo=timezone.utc),
                         monotonic_provider=lambda: clock[0],
                         node_registry=NodeRegistry(tmp_path / "nodes.json"))
    return manager, clock, sent, published


def report(manager, revision, logical_id, *, ack=False):
    payload = {"protocol_version": 2, "node_id": NODE, "revision": revision,
               "logical_id": logical_id, "registration_state": "registered",
               "capabilities": 3, "connected_sensors": ["sen66"],
               "boot_id": "0123456789abcdef", "seq": revision, "uptime_s": revision * 30}
    if ack:
        payload["result"] = "applied"
    suffix = "registration/ack" if ack else "status"
    manager.handle_node_mqtt(f"omk/node/{NODE}/{suffix}", json.dumps(payload).encode())


def configs(sent):
    return [(value, flags) for topic, value, flags in sent if topic.endswith("/config")]


def test_config_and_endpoint_ack_loss_converge_using_existing_gateway_retry(control):
    manager, clock, sent, _ = control
    report(manager, 1, "old")
    manager.request_node_registration(NODE, "new")
    expected = {"protocol_version": 2, "revision": 2, "logical_id": "new"}
    assert configs(sent) == [(expected, {"qos": 1, "retain": True})]
    # Broker PUBACK succeeds, first Mesh config is dropped. No endpoint proof.
    clock[0] = 4.999
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 1
    clock[0] = 5
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 2
    # The second config applies, but its endpoint ACK is lost. Replay continues.
    clock[0] = 10
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 3 and all(p == expected for p, _ in configs(sent))
    report(manager, 1, "old", ack=True)
    clock[0] = 15
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 4
    report(manager, 2, "new")  # Fresh status is matching endpoint confirmation.
    clock[0] = 30
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 4
    assert manager.node_registry.list()[NODE]["logical_id"] == "new"


def test_config_retry_does_not_duplicate_an_unacknowledged_broker_publication(control):
    manager, clock, sent, published = control
    report(manager, 1, "old")
    published[0] = False
    manager.request_node_registration(NODE, "new")
    for t in range(5, 100, 5):
        clock[0] = t
        manager.retry_pending_node_configs()
    assert len(configs(sent)) == 1
    published[0] = True
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == 2


def test_old_applied_reply_cannot_complete_a_newer_pending_config(control):
    manager, clock, sent, _ = control
    report(manager, 1, "old")
    manager.request_node_registration(NODE, "first")
    manager.request_node_registration(NODE, "latest")
    report(manager, 2, "first", ack=True)
    clock[0] = 10
    manager.retry_pending_node_configs()
    assert configs(sent)[-1][0] == {"protocol_version": 2, "revision": 3, "logical_id": "latest"}
    count = len(configs(sent))
    report(manager, 3, "latest", ack=True)
    clock[0] = 20
    manager.retry_pending_node_configs()
    assert len(configs(sent)) == count
