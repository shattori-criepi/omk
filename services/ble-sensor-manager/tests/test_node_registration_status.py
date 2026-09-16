"""Restore Node-owned registration without BLE discovery or a retained ACK."""
import json

import pytest

from omk_ble import main as ble_main
from omk_ble.node_registry import NodeRegistry
from omk_ble.registry import SensorRegistry
from omk_ble.service import BleManager


NODE = "020000000001"
OTHER = "020000000002"


class Publisher:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, **kwargs):
        self.messages.append((topic, json.loads(payload), kwargs))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("omk_ble.service.now_iso", lambda: "2026-09-16T00:00:00+00:00")
    return BleManager(SensorRegistry(tmp_path / "sensors.json"), Publisher(),
                      node_registry=NodeRegistry(tmp_path / "nodes.json"))


def status(manager, node_id=NODE, **fields):
    value = {"protocol_version": 1, "node_id": node_id, "registration_state": "registered",
             "capabilities": 3, "connected_sensors": ["sen66"], **fields}
    manager.handle_node_mqtt(f"omk/node/{node_id}/registration/status", json.dumps(value).encode())


def ack(manager, logical_id):
    value = {"protocol_version": 1, "node_id": NODE, "registration_state": "registered", "logical_id": logical_id}
    manager.handle_node_mqtt(f"omk/node/{NODE}/registration/ack", json.dumps(value).encode())


@pytest.mark.parametrize("logical_id", ["sen66-001", "A_" + "x" * 46])
def test_fresh_gateway_restores_complete_registration_and_api_after_restart(manager, monkeypatch, logical_id):
    assert not manager.node_registry.path.exists()
    status(manager, logical_id=logical_id)
    saved = manager.node_registry.list()[NODE]
    assert saved == {"node_id": NODE, "protocol_version": 1, "logical_id": logical_id,
                     "registration_state": "registered", "capabilities": 3,
                     "connected_sensors": ["sen66"], "mqtt_status_seen_at": "2026-09-16T00:00:00+00:00"}
    assert not manager._mqtt.messages  # No config or manufactured ACK needed.
    restarted = BleManager(manager.registry, node_registry=NodeRegistry(manager.node_registry.path))
    monkeypatch.setattr(ble_main, "manager", restarted)
    node = ble_main.nodes()["nodes"][0]
    assert node["node_id"] == NODE
    assert node["logical_id"] == logical_id
    assert node["registration_state"] == "registered"
    assert node["attached_sensors"] == ["SEN66"]
    assert node["online"] is False  # Retained registration is not a heartbeat.


def test_repeated_status_is_idempotent_and_only_refreshes_receipt_time(manager, monkeypatch):
    status(manager, logical_id="sen66-001")
    before = manager.node_registry.list()
    status(manager, logical_id="sen66-001")
    assert manager.node_registry.list() == before
    monkeypatch.setattr("omk_ble.service.now_iso", lambda: "2026-09-16T00:01:00+00:00")
    status(manager, logical_id="sen66-001")
    before[NODE]["mqtt_status_seen_at"] = "2026-09-16T00:01:00+00:00"
    assert manager.node_registry.list() == before


@pytest.mark.parametrize("existing_id", [None, "sen66-001"])
def test_legacy_status_is_accepted_without_removing_existing_id(manager, existing_id):
    if existing_id:
        manager.node_registry.update(NODE, logical_id=existing_id, ack_seen_at="before", unrelated="preserved")
    status(manager)
    saved = manager.node_registry.list()[NODE]
    assert saved["registration_state"] == "registered"
    assert saved["connected_sensors"] == ["sen66"]
    assert saved.get("logical_id") == existing_id
    if existing_id:
        assert saved["ack_seen_at"] == "before"
        assert saved["unrelated"] == "preserved"


@pytest.mark.parametrize("fields", [{}, {"logical_id": None}, {"logical_id": "stale-id"}])
def test_provisioned_status_clears_registration_only(manager, fields):
    manager.node_registry.update(NODE, logical_id="sen66-001", requested_logical_id="sen66-002",
                                 request_state="request_sent", ack_seen_at="before", unrelated="preserved")
    status(manager, registration_state="provisioned", **fields)
    saved = manager.node_registry.list()[NODE]
    assert {"logical_id", "requested_logical_id", "request_state", "ack_seen_at"}.isdisjoint(saved)
    assert saved["registration_state"] == "provisioned"
    assert saved["capabilities"] == 3
    assert saved["unrelated"] == "preserved"


@pytest.mark.parametrize("logical_id", [None, "", "x" * 49, "invalid/id", "space id", "センサ", 42, [], {}])
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_registered_id_is_rejected_without_overwriting_registry(manager, logical_id, existing):
    if existing:
        status(manager, logical_id="sen66-001")
    before = manager.node_registry.list()
    status(manager, logical_id=logical_id)
    assert manager.node_registry.list() == before


@pytest.mark.parametrize("owner_field", ["logical_id", "requested_logical_id"])
def test_status_cannot_take_another_nodes_assigned_or_pending_id(manager, owner_field):
    manager.node_registry.update(OTHER, **{owner_field: "sen66-001"})
    status(manager, logical_id="sen66-002")
    before = manager.node_registry.list()
    status(manager, logical_id="sen66-001")
    assert manager.node_registry.list() == before


def test_duplicate_status_on_fresh_registry_does_not_register_second_node(manager):
    status(manager, logical_id="sen66-001")
    status(manager, node_id=OTHER, logical_id="sen66-001")
    assert set(manager.node_registry.list()) == {NODE}


def test_restored_id_is_reserved_for_subsequent_registration_requests_and_acks(manager):
    status(manager, node_id=OTHER, logical_id="sen66-001")
    status(manager, registration_state="provisioned")
    with pytest.raises(ValueError, match="already assigned"):
        manager.request_node_registration(NODE, "sen66-001")
    ack(manager, "sen66-001")
    assert "logical_id" not in manager.node_registry.list()[NODE]


def test_status_does_not_complete_pending_edit_but_ack_still_does(manager):
    status(manager, logical_id="sen66-001")
    manager.request_node_registration(NODE, "sen66-002")
    for logical_id in ("sen66-001", "sen66-002"):
        status(manager, logical_id=logical_id)
        saved = manager.node_registry.list()[NODE]
        assert saved["logical_id"] == "sen66-001"
        assert saved["requested_logical_id"] == "sen66-002"
        assert saved["request_state"] == "request_sent"
        assert "ack_seen_at" not in saved
    ack(manager, "sen66-001")  # Old ACK must not complete the new request.
    assert manager.node_registry.list()[NODE]["request_state"] == "request_sent"
    ack(manager, "sen66-002")
    status(manager, logical_id="sen66-002")
    saved = manager.node_registry.list()[NODE]
    assert saved["logical_id"] == "sen66-002"
    assert saved["request_state"] == "registered"
    assert saved["requested_logical_id"] is None
    assert saved["ack_seen_at"] == saved["mqtt_status_seen_at"]


def test_old_retained_status_cannot_undo_explicit_removal(manager):
    status(manager, logical_id="sen66-001")
    manager.remove_node_registration(NODE)
    before = manager.node_registry.list()
    status(manager, logical_id="sen66-001")
    assert manager.node_registry.list() == before
    status(manager, registration_state="provisioned")
    assert "logical_id" not in manager.node_registry.list()[NODE]
    manager.request_node_registration(NODE, "sen66-002")
    ack(manager, "sen66-002")
    assert manager.node_registry.list()[NODE]["logical_id"] == "sen66-002"
