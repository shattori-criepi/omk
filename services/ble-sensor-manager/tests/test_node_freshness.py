from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from omk_ble.service import BleManager
from omk_ble.registry import SensorRegistry
from omk_ble.node_registry import NodeRegistry

NOW = datetime(2026, 9, 11, 6, tzinfo=timezone.utc)
NODE = '55f94c790e12'


@pytest.mark.parametrize('source,threshold', [('discovery', 30), ('relay_last_seen_at', 60), ('sen66_last_seen_at', 60)])
@pytest.mark.parametrize('age', [0, 30, 60, 61, 1200, 7200])
def test_node_activity_freshness_preserves_metadata(tmp_path, source, threshold, age):
    registry = NodeRegistry(tmp_path / 'nodes.json')
    registry.update(NODE, registration_state='registered', logical_id='sensor-001', capabilities=3,
                    mqtt_status_seen_at=NOW.isoformat())
    timestamp = (NOW - timedelta(seconds=age)).isoformat()
    manager = BleManager(SensorRegistry(tmp_path / 'sensors.json'), node_registry=registry, now_provider=lambda: NOW)
    if source == 'discovery':
        manager.node_observations[NODE] = SimpleNamespace(received_at=timestamp, raw={}, values={'capabilities': ['ble_scan', 'sen66'], 'provisioning_state': 'registered'})
    else:
        registry.update(NODE, **{source: timestamp})
    before = registry.list()
    node = manager.node_list()[0]
    assert node['online'] is (age <= threshold)
    assert node['relay_active'] is (source == 'relay_last_seen_at' and age <= threshold)
    assert node['logical_id'] == 'sensor-001'
    assert node['registration_state'] == 'registered'
    assert node['capabilities'] == ['ble_scan', 'sen66']
    assert registry.list() == before


@pytest.mark.parametrize('timestamp', [None, '', 'invalid', 123, '2026-09-11T06:00:00', '99999-01-01', '2027-01-01T00:00:00Z'])
def test_invalid_or_missing_activity_is_offline(tmp_path, timestamp):
    registry = NodeRegistry(tmp_path / 'nodes.json')
    registry.update(NODE, relay_last_seen_at=timestamp, sen66_last_seen_at=timestamp, mqtt_status_seen_at=NOW.isoformat())
    manager = BleManager(SensorRegistry(tmp_path / 'sensors.json'), node_registry=registry, now_provider=lambda: NOW)
    manager.node_observations[NODE] = SimpleNamespace(received_at=timestamp, raw={}, values={'capabilities': [], 'provisioning_state': 'provisioned'})
    node = manager.node_list()[0]
    assert node['online'] is False
    assert node['relay_active'] is False


def test_old_registration_and_fresh_discovery_do_not_activate_stale_relay(tmp_path):
    registry = NodeRegistry(tmp_path / 'nodes.json')
    registry.update(NODE, mqtt_status_seen_at='2026-09-11T12:52:04+09:00', relay_last_seen_at='2026-09-11T12:52:53+09:00')
    manager = BleManager(SensorRegistry(tmp_path / 'sensors.json'), node_registry=registry, now_provider=lambda: NOW)
    assert manager.node_list()[0]['online'] is False
    manager.node_observations[NODE] = SimpleNamespace(received_at='2026-09-11T15:00:00+09:00', raw={}, values={'capabilities': ['ble_scan'], 'provisioning_state': 'provisioned'})
    assert manager.node_list()[0]['online'] is True
    assert manager.node_list()[0]['relay_active'] is False
