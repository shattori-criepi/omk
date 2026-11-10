"""Native Mesh control converges after loss/reordering without proxy MQTT sessions."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
        self.rc = 0
        self.before_publish = None
        self.connected = True
        self.published = True

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, **kwargs):
        if self.before_publish:
            self.before_publish(topic, payload)
        self.messages.append((topic, json.loads(payload) if payload else None, kwargs))
        return SimpleNamespace(rc=self.rc, is_published=lambda: self.published)


@pytest.fixture
def manager(tmp_path):
    clock = [datetime(2026, 11, 10, tzinfo=timezone.utc)]
    result = BleManager(SensorRegistry(tmp_path / "sensors.json"), Publisher(),
                        now_provider=lambda: clock[0], monotonic_provider=lambda: clock[0].timestamp(),
                        node_registry=NodeRegistry(tmp_path / "nodes.json"))
    result.clock = clock
    return result


def report(manager, *, node_id=NODE, kind="registration/status", revision=1, logical_id="sen66-001", retain=False, **fields):
    value = {"protocol_version": 2, "node_id": node_id, "revision": revision,
             "registration_state": "registered" if logical_id else "provisioned",
             "capabilities": 3, "connected_sensors": ["sen66"], "logical_id": logical_id,
             "boot_id": "0000000000000001", "seq": 1, "uptime_s": 30, **fields}
    manager.handle_node_mqtt(f"omk/node/{node_id}/{kind}", json.dumps(value).encode(), retain=retain)


def configs(manager):
    return [payload for topic, payload, _ in manager._mqtt.messages if topic.endswith("/config")]


def test_fresh_gateway_restores_node_owned_revision_without_retained_liveness(manager):
    report(manager, revision=17, retain=True)
    saved = manager.node_registry.list()[NODE]
    assert saved["revision"] == 17 and saved["logical_id"] == "sen66-001"
    assert not manager.node_list()[0]["online"]
    manager.request_node_registration(NODE, "sen66-002")
    assert configs(manager)[-1] == {"protocol_version": 2, "revision": 18, "logical_id": "sen66-002"}


def test_persist_before_publish_and_replay_after_gateway_broker_restart(manager):
    report(manager)
    def before(topic, payload):
        if topic.endswith("/config"):
            stored = manager.node_registry.list()[NODE]
            assert stored["desired_revision"] == 2
            assert stored["desired_logical_id"] == "sen66-002"
    manager._mqtt.before_publish = before
    manager._mqtt.rc = 4
    with pytest.raises(RuntimeError, match="pending"):
        manager.request_node_registration(NODE, "sen66-002")
    publisher = Publisher()
    restarted = BleManager(manager.registry, publisher, node_registry=NodeRegistry(manager.node_registry.path))
    restarted.mqtt_nodes_connected()
    assert configs(restarted)[-1]["revision"] == 2
    assert publisher.messages[0][2] == {"qos": 1, "retain": True}


def test_persistence_failure_sends_no_config(manager, monkeypatch):
    report(manager)
    before = list(manager._mqtt.messages)
    def fail(*args):
        raise RuntimeError("disk failure")
    monkeypatch.setattr(manager.node_registry, "_write", fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        manager.request_node_registration(NODE, "sen66-002")
    assert manager._mqtt.messages == before


def test_corrupt_registry_is_not_silently_replaced(manager):
    manager.node_registry.path.write_text("{broken")
    with pytest.raises(RuntimeError, match="Cannot read"):
        manager.request_node_registration(NODE, "sen66-002")
    assert not manager._mqtt.messages
    assert manager.node_registry.path.read_text() == "{broken"


def test_root_change_replays_pending_then_matching_endpoint_snapshot_completes(manager):
    report(manager)
    manager.request_node_registration(NODE, "sen66-002")
    manager._mqtt.messages.clear()
    manager.clock[0] += timedelta(seconds=30)
    report(manager, kind="status", seq=4, uptime_s=120)  # New root forwards Node's old applied state.
    assert configs(manager)[-1]["revision"] == 2
    report(manager, kind="status", revision=2, logical_id="sen66-002", seq=5, uptime_s=150)
    saved = manager.node_registry.list()[NODE]
    assert saved["request_state"] == "registered" and saved["logical_id"] == "sen66-002"
    manager._mqtt.messages.clear()
    manager.mqtt_nodes_connected()
    assert not configs(manager)


def test_stale_ack_and_error_ack_cannot_complete_newer_request(manager):
    report(manager)
    manager.request_node_registration(NODE, "sen66-002")
    manager.request_node_registration(NODE, "sen66-003")
    report(manager, kind="registration/ack", revision=2, logical_id="sen66-002", result="applied")
    report(manager, kind="registration/ack", revision=3, logical_id="sen66-003", result="error")
    assert manager.node_registry.list()[NODE]["logical_id"] == "sen66-001"
    report(manager, kind="registration/ack", revision=3, logical_id="sen66-003", result="applied")
    before = manager.node_registry.list()[NODE]
    report(manager, kind="registration/ack", revision=2, logical_id="sen66-002", result="applied")
    assert manager.node_registry.list()[NODE] == before


def test_removal_is_persistent_tombstone_then_verified_cleanup(manager):
    report(manager, revision=4)
    manager.remove_node_registration(NODE)
    assert configs(manager)[-1] == {"protocol_version": 2, "revision": 5, "logical_id": None}
    report(manager, revision=4, retain=True)
    assert "logical_id" not in manager.node_registry.list()[NODE]
    assert configs(manager)[-1]["revision"] == 5
    report(manager, kind="registration/ack", revision=5, logical_id=None, result="applied")
    assert configs(manager)[-1] is None
    assert manager.node_registry.list()[NODE]["request_state"] == "removed"
    report(manager, revision=4, retain=True)
    assert "logical_id" not in manager.node_registry.list()[NODE]


def test_late_cleanup_cannot_erase_newer_desired_config(manager):
    report(manager)
    manager.remove_node_registration(NODE)
    report(manager, revision=2, logical_id=None)
    manager.request_node_registration(NODE, "new-name")
    manager._mqtt.messages.clear()
    report(manager, kind="registration/ack", revision=2, logical_id=None, result="applied")
    assert None not in configs(manager)
    assert manager.node_registry.list()[NODE]["desired_logical_id"] == "new-name"


@pytest.mark.parametrize("legacy", [{"registration_revoked": True}, {"requested_logical_id": "new-name"}])
def test_old_registry_intent_migrates_above_endpoint_revision(manager, legacy):
    manager.node_registry.update(NODE, logical_id="old-name", **legacy)
    report(manager, revision=9, logical_id="old-name")
    assert configs(manager)[-1]["revision"] == 10
    assert configs(manager)[-1]["logical_id"] == legacy.get("requested_logical_id")


def test_physical_clear_supersedes_completed_request_but_not_pending_request(manager):
    report(manager)
    manager.request_node_registration(NODE, "new-name")
    report(manager, revision=2, logical_id="new-name")
    report(manager, revision=3, logical_id=None)
    assert "logical_id" not in manager.node_registry.list()[NODE]
    manager.request_node_registration(NODE, "another-name")
    report(manager, revision=5, logical_id=None)
    assert configs(manager)[-1] == {"protocol_version": 2, "revision": 6, "logical_id": "another-name"}


def test_duplicate_logical_id_is_rejected(manager):
    report(manager)
    report(manager, node_id=OTHER, logical_id=None)
    with pytest.raises(ValueError, match="already assigned"):
        manager.request_node_registration(OTHER, "sen66-001")
    report(manager, node_id=OTHER, logical_id="sen66-001")
    assert "logical_id" not in manager.node_registry.list()[OTHER]


@pytest.mark.parametrize("fields", [{"protocol_version": 1}, {"revision": -1}, {"revision": True},
                                     {"revision": 2**32}, {"logical_id": "bad/id"}, {"node_id": OTHER}])
def test_invalid_state_does_not_change_registry(manager, fields):
    report(manager)
    before = manager.node_registry.list()
    if "node_id" in fields:
        value = {"protocol_version": 2, "node_id": OTHER, "revision": 1}
        manager.handle_node_mqtt(f"omk/node/{NODE}/status", json.dumps(value).encode())
    else:
        report(manager, **fields)
    assert manager.node_registry.list() == before


def test_heartbeat_expiry_retained_replay_duplicate_and_old_boot_are_not_liveness(manager):
    report(manager)
    report(manager, kind="status", retain=True)
    assert not manager.node_list()[0]["online"]
    report(manager, kind="status")
    assert manager.node_list()[0]["online"]
    manager.clock[0] += timedelta(seconds=119)
    assert manager.node_list()[0]["online"]
    report(manager, kind="status")  # duplicate must not extend deadline
    manager.clock[0] += timedelta(seconds=1)
    manager.refresh_node_availability()
    assert not manager.node_list()[0]["online"]
    assert manager._mqtt.messages[-1][1]["status"] == "offline"
    report(manager, kind="status", boot_id="0000000000000002", seq=0, uptime_s=1)
    assert manager.node_list()[0]["online"]
    manager.clock[0] += timedelta(seconds=120)
    report(manager, kind="status", seq=2, uptime_s=60)  # retired boot after root change
    assert not manager.node_list()[0]["online"]
    assert manager.node_registry.list()[NODE]["heartbeat_boot_id"] == "0000000000000002"


def test_root_lwt_never_fans_out_and_stream_sequences_are_independent(manager):
    report(manager, kind="status", seq=100)
    telemetry = {"protocol_version": 2, "node_id": NODE, "revision": 1, "boot_id": "0000000000000001",
                 "seq": 1, "device_id": "sen66-001", "meta": {"seq": 10}}
    manager.clock[0] += timedelta(seconds=100)
    manager.handle_sen66_mqtt(f"omk-relay/{NODE}/sen66/raw", json.dumps(telemetry).encode())
    manager.handle_node_mqtt("omk-mesh/root/status", b'{"status":"offline"}')
    manager.clock[0] += timedelta(seconds=100)
    assert manager.node_list()[0]["online"]
    manager.handle_sen66_mqtt(f"omk-relay/{NODE}/sen66/raw", json.dumps(telemetry).encode())
    manager.clock[0] += timedelta(seconds=20)
    assert not manager.node_list()[0]["online"]


def test_revision_exhaustion_fails_before_publish(manager):
    report(manager, revision=2**32-1)
    before = list(manager._mqtt.messages)
    with pytest.raises(RuntimeError, match="exhausted"):
        manager.request_node_registration(NODE, "next-name")
    assert manager._mqtt.messages == before


def test_rename_cleans_old_retained_availability_and_survives_cleanup_failure(manager):
    report(manager, kind="status")
    manager.request_node_registration(NODE, "sen66-002")
    manager._mqtt.rc = 4
    report(manager, kind="registration/ack", revision=2, logical_id="sen66-002", result="applied")
    assert manager.node_registry.list()[NODE]["retired_logical_ids"] == ["sen66-001"]
    manager._mqtt.rc = 0
    manager._mqtt.messages.clear()
    manager.refresh_node_availability()
    assert ("omk/sen66-001/status", None, {"qos": 0, "retain": True}) in manager._mqtt.messages
    assert manager.node_registry.list()[NODE]["retired_logical_ids"] == []
    assert manager._mqtt.messages[-1][1]["device_id"] == "sen66-002"


def test_retired_boot_rejected_across_streams_and_gateway_restart(manager):
    report(manager, kind="status", boot_id="0000000000000001")
    report(manager, kind="status", boot_id="0000000000000002")
    restarted = BleManager(manager.registry, manager._mqtt, now_provider=lambda: manager.clock[0],
                          node_registry=NodeRegistry(manager.node_registry.path))
    manager.clock[0] += timedelta(seconds=120)
    telemetry = {"protocol_version": 2, "node_id": NODE, "revision": 1,
                 "boot_id": "0000000000000001", "seq": 10, "device_id": "sen66-001"}
    restarted.handle_sen66_mqtt(f"omk-relay/{NODE}/sen66/raw", json.dumps(telemetry).encode())
    assert not restarted.node_list()[0]["online"]


def test_main_subscribes_heartbeat_and_propagates_retained_flag(manager, monkeypatch):
    monkeypatch.setattr(ble_main, "manager", manager)
    subscriptions = []
    client = SimpleNamespace(subscribe=lambda topic, **kw: subscriptions.append((topic, kw)))
    ble_main._on_mqtt_connect(client, None, None, 0, None)
    assert ("omk/node/+/status", {"qos": 0}) in subscriptions
    value = {"protocol_version": 2, "node_id": NODE, "revision": 1, "logical_id": "sen66-001",
             "registration_state": "registered", "capabilities": 3, "connected_sensors": [],
             "boot_id": "0000000000000001", "seq": 1, "uptime_s": 30}
    ble_main._on_mqtt_message(client, None, SimpleNamespace(topic=f"omk/node/{NODE}/status",
                               payload=json.dumps(value).encode(), retain=True))
    assert manager.node_list() == []
    ble_main._on_mqtt_message(client, None, SimpleNamespace(topic=f"omk/node/{NODE}/status",
                               payload=json.dumps(value).encode(), retain=False))
    assert manager.node_list()[0]["online"]


def test_pending_config_retries_without_new_heartbeat_or_broker_reconnect(manager):
    report(manager)
    manager.request_node_registration(NODE, "new-name")
    # Broker accepted, but Root-to-Node delivery was lost; there is no ACK.
    desired = configs(manager)[-1]
    manager._mqtt.messages.clear()
    manager.clock[0] += timedelta(seconds=5)
    manager.retry_pending_node_configs()
    assert configs(manager) == [desired]
    report(manager, revision=2, logical_id="new-name")
    manager._mqtt.messages.clear()
    manager.retry_pending_node_configs()
    assert configs(manager) == []


def test_revoked_owner_reserves_name_until_applied_then_rejects_delayed_state(manager):
    report(manager, kind="status")
    manager.remove_node_registration(NODE)
    report(manager, node_id=OTHER, logical_id=None)
    with pytest.raises(ValueError, match="already assigned"):
        manager.request_node_registration(OTHER, "sen66-001")
    manager.clock[0] += timedelta(seconds=5)
    report(manager, kind="status", seq=2, uptime_s=60)
    assert configs(manager)[-1] == {"protocol_version": 2, "revision": 2, "logical_id": None}
    report(manager, kind="registration/ack", revision=2, logical_id=None, result="applied")
    manager.request_node_registration(OTHER, "sen66-001")
    report(manager, node_id=OTHER, revision=2)
    manager._mqtt.messages.clear()
    # A delayed report from the old Root must never resurrect its old owner.
    report(manager, revision=1, retain=True)
    assert "logical_id" not in manager.node_registry.list()[NODE]
    assert manager.node_registry.list()[OTHER]["logical_id"] == "sen66-001"


def test_v1_discovery_and_v2_heartbeat_share_one_physical_node(manager):
    from omk_ble.omk_node import decode, OMK_NODE_SERVICE_UUID
    observation = decode(-42, {OMK_NODE_SERVICE_UUID: bytes([1, 1, 0, 3]) + bytes.fromhex(NODE)},
                         manager.clock[0].isoformat(), "02:00:00:00:00:aa")
    manager.record_advertisement(observation)
    assert len(manager.node_list()) == 1 and not manager.node_list()[0]["online"]
    report(manager, kind="status")
    nodes = manager.node_list()
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == NODE and nodes[0]["logical_id"] == "sen66-001"
    assert nodes[0]["ble_address"] == "02:00:00:00:00:aa" and nodes[0]["online"]


def test_relay_liveness_tracks_origin_node_not_forwarding_root(manager):
    report(manager, kind="registration/status")
    report(manager, node_id=OTHER, kind="registration/status", logical_id="root-node")
    raw = {"protocol_version": 2, "revision": 1, "node_id": NODE,
           "relay_node_id": NODE, "boot_id": "0000000000000001", "seq": 10,
           "ble_address": "020000000040", "rssi": -40,
           "manufacturer_data": [{"company_id": 0x0969, "data": "020000000040"}],
           "service_data": []}
    manager.handle_relay_mqtt(f"omk-relay/{NODE}/ble/raw", json.dumps(raw).encode())
    nodes = {node["node_id"]: node for node in manager.node_list()}
    assert nodes[NODE]["online"] and not nodes[OTHER]["online"]
    previous = manager.node_registry.list()
    raw["node_id"] = OTHER
    raw["seq"] = 11
    manager.handle_relay_mqtt(f"omk-relay/{NODE}/ble/raw", json.dumps(raw).encode())
    assert manager.node_registry.list() == previous


def sen66(manager, node_id=NODE, revision=1, logical_id="sen66-001", seq=1, **kwargs):
    value = {"protocol_version": 2, "node_id": node_id, "revision": revision,
             "device_id": logical_id, "boot_id": "0000000000000001", "seq": seq,
             "temperature_celsius": 23.5, "co2_ppm": 600}
    raw = json.dumps(value).encode()
    manager.handle_sen66_mqtt(f"omk-relay/{node_id}/sen66/raw", raw, **kwargs)
    return value


def measurements(manager):
    return [(topic, payload, flags) for topic, payload, flags in manager._mqtt.messages
            if topic.startswith("omk/") and topic.endswith("/sen66")]


def test_reassignment_filters_delayed_root_outbox_before_all_public_subscribers(manager):
    report(manager)
    expected = sen66(manager)
    assert measurements(manager) == [("omk/sen66-001/sen66", expected, {"qos": 0, "retain": False})]
    manager.remove_node_registration(NODE)  # Node absent; revocation is not applied.
    report(manager, node_id=OTHER, logical_id=None)
    with pytest.raises(ValueError, match="already assigned"):
        manager.request_node_registration(OTHER, "sen66-001")
    manager._mqtt.messages.clear()
    sen66(manager, seq=2)
    assert not measurements(manager)
    # Lost ACK is resolved by a matching state forwarded by a replacement Root.
    report(manager, kind="status", revision=2, logical_id=None, seq=3, uptime_s=60)
    manager.request_node_registration(OTHER, "sen66-001")
    report(manager, node_id=OTHER, revision=2)
    manager._mqtt.messages.clear()
    sen66(manager, seq=4)  # Old Root/SDK backlog, received after ID reuse.
    new = sen66(manager, node_id=OTHER, revision=2)
    assert measurements(manager) == [("omk/sen66-001/sen66", new, {"qos": 0, "retain": False})]
    # The gateway restarts with the same authoritative ownership/revisions.
    restarted = BleManager(manager.registry, Publisher(), node_registry=NodeRegistry(manager.node_registry.path))
    sen66(restarted, seq=5)
    sen66(restarted, node_id=OTHER, revision=1, seq=2)
    sen66(restarted, node_id=OTHER, revision=2, seq=2, retain=True)
    assert not measurements(restarted)
    sen66(restarted, node_id=OTHER, revision=2, seq=2)
    assert len(measurements(restarted)) == 1


def test_superseded_names_remain_reserved_across_restart_until_latest_apply(manager):
    report(manager)
    report(manager, node_id=OTHER, logical_id=None)
    manager.request_node_registration(NODE, "second")
    manager.request_node_registration(NODE, "third")
    manager.remove_node_registration(NODE)
    restarted = BleManager(manager.registry, Publisher(), node_registry=NodeRegistry(manager.node_registry.path))
    for name in ("sen66-001", "second", "third"):
        with pytest.raises(ValueError, match="already assigned"):
            restarted.request_node_registration(OTHER, name)
    report(restarted, kind="registration/ack", revision=2, logical_id="second", result="applied")
    assert set(restarted.node_registry.list()[NODE]["reserved_logical_ids"]) == {"sen66-001", "second", "third"}
    report(restarted, kind="registration/ack", revision=4, logical_id=None, result="applied")
    assert restarted.node_registry.list()[NODE]["reserved_logical_ids"] == []
    restarted.request_node_registration(OTHER, "second")


def test_reusing_name_on_same_node_rejects_earlier_registration_generation(manager):
    report(manager)
    manager.remove_node_registration(NODE)
    report(manager, revision=2, logical_id=None)
    manager.request_node_registration(NODE, "sen66-001")
    report(manager, revision=3)
    manager._mqtt.messages.clear()
    sen66(manager, revision=1, seq=100)
    assert not measurements(manager)
    sen66(manager, revision=3, seq=101)
    assert len(measurements(manager)) == 1


def test_raw_sen66_dispatch_and_public_topic_does_not_loop(manager, monkeypatch):
    report(manager)
    monkeypatch.setattr(ble_main, "manager", manager)
    subscriptions = []
    client = SimpleNamespace(subscribe=lambda topic, **kw: subscriptions.append(topic))
    ble_main._on_mqtt_connect(client, None, None, 0, None)
    assert "omk-relay/+/sen66/raw" in subscriptions and "omk/+/sen66" not in subscriptions
    value = sen66(manager)
    manager._mqtt.messages.clear()
    value["seq"] += 1
    ble_main._on_mqtt_message(client, None, SimpleNamespace(topic=f"omk-relay/{NODE}/sen66/raw",
                              payload=json.dumps(value).encode(), retain=False))
    assert len(measurements(manager)) == 1
    ble_main._on_mqtt_message(client, None, SimpleNamespace(topic="omk/sen66-001/sen66",
                              payload=json.dumps(value).encode(), retain=False))
    assert len(measurements(manager)) == 1


def test_disconnected_periodic_retry_never_grows_real_paho_outbox(manager):
    import paho.mqtt.client as mqtt
    report(manager)
    manager._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    manager._mqtt.max_queued_messages_set(64)
    manager.request_node_registration(NODE, "second")
    for _ in range(1000):
        manager.clock[0] += timedelta(seconds=5)
        manager.retry_pending_node_configs()
    manager.request_node_registration(NODE, "latest")
    assert len(manager._mqtt._out_messages) == 0
    manager._mqtt = Publisher()
    manager.mqtt_nodes_connected()
    assert configs(manager) == [{"protocol_version": 2, "revision": 3, "logical_id": "latest"}]
    assert ble_main.client.max_queued_messages == 64


def test_inflight_and_recent_same_revision_are_not_republished(manager):
    report(manager)
    manager._mqtt.published = False
    manager.request_node_registration(NODE, "new-name")
    manager._mqtt.messages.clear()
    for _ in range(20):
        manager.clock[0] += timedelta(seconds=5)
        manager.retry_pending_node_configs()
    assert not configs(manager)  # Paho already owns this unacknowledged publish.
    manager._mqtt.published = True
    manager.retry_pending_node_configs()
    assert len(configs(manager)) == 1
    manager.retry_pending_node_configs()
    report(manager, kind="status", seq=2, uptime_s=60)
    assert len(configs(manager)) == 1  # Heartbeat and timer share the same throttle.
    manager.clock[0] += timedelta(seconds=5)
    manager.retry_pending_node_configs()
    assert len(configs(manager)) == 2  # Broker ACK is not a Mesh endpoint ACK.


def test_queue_full_leaves_latest_intent_pending_until_retry(manager):
    import paho.mqtt.client as mqtt
    report(manager)
    manager._mqtt.rc = mqtt.MQTT_ERR_QUEUE_SIZE
    with pytest.raises(RuntimeError, match="pending"):
        manager.request_node_registration(NODE, "latest")
    assert manager.node_registry.list()[NODE]["desired_logical_id"] == "latest"
    manager._mqtt.rc = 0
    manager.retry_pending_node_configs()
    assert configs(manager)[-1]["logical_id"] == "latest"
