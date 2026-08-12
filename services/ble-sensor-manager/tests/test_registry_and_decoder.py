from pathlib import Path

import pytest

from omk_ble.registry import RegistryError, SensorRegistry
from omk_ble.models import RegisteredSensor
from omk_ble.switchbot import METER_SERVICE_UUID, SWITCHBOT_COMPANY_ID, decode
from omk_ble.service import BleManager


def test_meter_advertisement_decodes_documented_environment_fields() -> None:
    decoded = decode(
        "AA:BB:CC:DD:EE:01", -43, {SWITCHBOT_COMPANY_ID: b"\x01"},
        {METER_SERVICE_UUID: bytes([0x54, 0, 87, 0x03, 0x99, 52])},
        "2026-08-12T10:00:00+09:00",
    )
    assert decoded is not None
    assert decoded.model == "meter"
    assert decoded.values == {"temperature_c": 25.3, "relative_humidity_percent": 52, "battery_percent": 87}


def test_unknown_switchbot_and_malformed_meter_remain_safe_raw_candidates() -> None:
    decoded = decode("AA:BB:CC:DD:EE:02", -60, {SWITCHBOT_COMPANY_ID: b"\x01"}, {}, "now")
    assert decoded is not None and decoded.model == "unknown_switchbot" and decoded.values == {}
    malformed = decode("x", -1, {}, {METER_SERVICE_UUID: b"\x54"}, "now")
    assert malformed is not None
    assert malformed.model == "unknown_switchbot"
    assert malformed.sensor_type == "unknown"
    assert malformed.values == {}
    assert malformed.raw["service_data"][METER_SERVICE_UUID] == "54"


def test_non_switchbot_packet_is_not_classified_as_unknown_switchbot() -> None:
    assert decode("AA:BB:CC:DD:EE:FF", -70, {0xFFFF: b"\x01"}, {}, "now") is None


def test_pi_captured_meter_manufacturer_packet_decodes_temperature_and_humidity() -> None:
    decoded = decode(
        "CF:39:41:C7:ED:79", -28,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("cf3941c7ed79f40304992c")}, {}, "2026-08-12T10:00:00+09:00",
    )
    assert decoded is not None
    assert decoded.device_key == "switchbot:cf3941c7ed79"
    assert decoded.model == "meter"
    assert decoded.sensor_type == "environment"
    assert decoded.values == {"temperature_c": 25.4, "relative_humidity_percent": 44}


def test_pi_captured_co2_manufacturer_packets_decode_only_co2() -> None:
    first = decode("B0:E9:FE:54:96:C5", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fe5496c5b6e4059a2a003f02ad00")}, {}, "now")
    second = decode("B0:E9:FE:54:96:C5", -64, {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fe5496c5b9e4059a28003f028100")}, {}, "now")
    assert first is not None and first.model == "meter_pro_co2" and first.values == {"co2_ppm": 685}
    assert second is not None and second.values == {"co2_ppm": 641}


def test_short_and_unknown_manufacturer_packets_remain_raw_unknown_candidates() -> None:
    for packet in (b"", bytes.fromhex("cf3941c7ed79f40304"), bytes.fromhex("cf3941c7ed79000004992c")):
        decoded = decode("CF:39:41:C7:ED:79", -50, {SWITCHBOT_COMPANY_ID: packet}, {}, "now")
        assert decoded is not None
        assert decoded.model == "unknown_switchbot"
        assert decoded.values == {}


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
