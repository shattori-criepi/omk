from pathlib import Path

import pytest

from omk_ble.registry import RegistryError, SensorRegistry
from omk_ble.models import RegisteredSensor
from omk_ble.switchbot import METER_SERVICE_UUID, SWITCHBOT_COMPANY_ID, decode
from omk_ble.service import BleManager


def test_meter_advertisement_decodes_documented_environment_fields() -> None:
    decoded = decode("AA:BB:CC:DD:EE:01", -43, {SWITCHBOT_COMPANY_ID: b"\x01"}, {METER_SERVICE_UUID: bytes([0x54, 0, 2, 0x53, 52, 87])}, "2026-08-12T10:00:00+09:00")
    assert decoded is not None
    assert decoded.model == "meter"
    assert decoded.values == {"temperature_c": 25.3, "relative_humidity_percent": 52, "battery_percent": 87}


def test_unknown_switchbot_and_malformed_meter_remain_safe_raw_candidates() -> None:
    decoded = decode("AA:BB:CC:DD:EE:02", -60, {SWITCHBOT_COMPANY_ID: b"\x01"}, {}, "now")
    assert decoded is not None and decoded.model == "unknown_switchbot" and decoded.values == {}
    assert decode("x", -1, {}, {METER_SERVICE_UUID: b"\x54"}, "now") is None


def test_registry_validates_ids_and_prevents_duplicate_device_or_id(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    one = RegisteredSensor("switchbot:abc", "living-env-01", "environment", "switchbot", "meter", "living", "リビング")
    registry.register(one)
    assert registry.list() == [one]
    with pytest.raises(RegistryError, match="already"):
        registry.register(one)
    with pytest.raises(RegistryError, match="lowercase"):
        registry.register(RegisteredSensor("switchbot:def", "Bad_ID", "environment", "switchbot", "meter", "", "x"))


def test_offline_registration_is_kept_when_no_advertisement_is_seen(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:abc", "living-env-01", "environment", "switchbot", "meter", "", "リビング"))
    assert BleManager(registry).registered_list()[0]["status"] == "offline"
