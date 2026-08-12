import asyncio
from pathlib import Path

import pytest

from omk_ble.registry import RegistryError, SensorRegistry
from omk_ble.models import DecodedAdvertisement, RegisteredSensor
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


def test_pi_captured_meter_manufacturer_packets_decode_temperature_and_humidity() -> None:
    fixtures = (("cf3941c7ed79f40304992c", 25.4), ("cf3941c7ed79f80305992c", 25.5))
    for packet, temperature_c in fixtures:
        decoded = decode(
            "CF:39:41:C7:ED:79", -28,
            {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "2026-08-12T10:00:00+09:00",
        )
        assert decoded is not None
        assert decoded.device_key == "switchbot:cf3941c7ed79"
        assert decoded.model == "meter"
        assert decoded.sensor_type == "environment"
        assert decoded.values == {"temperature_c": temperature_c, "relative_humidity_percent": 44}


def test_pi_captured_co2_manufacturer_packets_decode_environment_measurements() -> None:
    fixtures = (
        ("B0:E9:FE:54:96:C5", "b0e9fe5496c5b6e4059a2a003f02ad00", {"co2_ppm": 685}),
        ("B0:E9:FE:54:96:C5", "b0e9fe5496c5b9e4059a28003f028100", {"co2_ppm": 641}),
        ("B0:E9:FE:58:15:CC", "b0e9fe5815ccf6e405982e0024020e00", {"temperature_c": 24.5, "relative_humidity_percent": 46, "co2_ppm": 526}),
        ("B0:E9:FE:58:15:CC", "b0e9fe5815ccf7e406982e0024027b00", {"temperature_c": 24.6, "relative_humidity_percent": 46, "co2_ppm": 635}),
    )
    for address, packet, expected in fixtures:
        decoded = decode(address, -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now")
        assert decoded is not None
        assert decoded.model == "meter_pro_co2"
        assert decoded.sensor_type == "environment"
        for key, value in expected.items():
            assert decoded.values[key] == value


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


def _candidate(device_key: str, *, rssi: int = -50, values: dict | None = None, model: str = "meter") -> DecodedAdvertisement:
    return DecodedAdvertisement(
        device_key=device_key, vendor="switchbot", model=model, sensor_type="environment",
        rssi=rssi, received_at="2026-08-12T10:00:00+09:00", values=values or {},
    )


def test_setup_candidate_order_is_discovery_order_and_updates_in_place(tmp_path: Path) -> None:
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    manager.record_advertisement(_candidate("switchbot:a", rssi=-80, values={"temperature_c": 20.0}))
    manager.record_advertisement(_candidate("switchbot:b", rssi=-30, values={"temperature_c": 21.0}))
    manager.record_advertisement(_candidate("switchbot:a", rssi=-10, values={"temperature_c": 22.0}))
    manager.record_advertisement(_candidate("switchbot:c", rssi=-60, model="unknown_switchbot"))
    candidates = manager.candidate_list()
    assert [item["device_key"] for item in candidates] == ["switchbot:a", "switchbot:b", "switchbot:c"]
    assert candidates[0]["rssi"] == -10
    assert candidates[0]["values"] == {"temperature_c": 22.0}


def test_latest_advertisement_replaces_all_candidate_data_without_reordering(tmp_path: Path) -> None:
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    first = DecodedAdvertisement("switchbot:a", "switchbot", "meter_pro_co2", "environment", -79, "first", {"co2_ppm": 577}, {"manufacturer_data": {"0969": "first"}})
    second = DecodedAdvertisement("switchbot:a", "switchbot", "meter_pro_co2", "environment", -31, "second", {"co2_ppm": 566}, {"manufacturer_data": {"0969": "second"}})
    manager.record_advertisement(first)
    manager.record_advertisement(second)
    candidates = manager.candidate_list()
    assert len(candidates) == 1
    assert candidates[0]["device_key"] == "switchbot:a"
    assert candidates[0]["rssi"] == -31
    assert candidates[0]["received_at"] == "second"
    assert candidates[0]["values"] == {"co2_ppm": 566}
    assert candidates[0]["raw"] == {"manufacturer_data": {"0969": "second"}}


def test_callback_updates_candidate_after_stop_and_new_scan_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    decoded = iter((_candidate("switchbot:a", rssi=-70, values={"temperature_c": 20.0}), _candidate("switchbot:a", rssi=-20, values={"temperature_c": 21.0})))
    monkeypatch.setattr("omk_ble.service.decode", lambda *_args: next(decoded))
    device = type("Device", (), {"address": "AA:BB:CC:DD:EE:FF"})()
    advertisement = type("Advertisement", (), {"rssi": -1, "manufacturer_data": {}, "service_data": {}})()

    async def start_collection() -> None:
        return None

    manager.start_collection = start_collection  # type: ignore[method-assign]

    async def exercise() -> None:
        await manager.start_scan(60)
        manager._on_detection(device, advertisement)
        await manager.stop_scan()
        await manager.start_scan(60)
        manager._on_detection(device, advertisement)
        await manager.stop_scan()

    asyncio.run(exercise())
    candidates = manager.candidate_list()
    assert [item["device_key"] for item in candidates] == ["switchbot:a"]
    assert candidates[0]["rssi"] == -20
    assert candidates[0]["values"] == {"temperature_c": 21.0}


def test_registered_device_is_excluded_then_moves_from_candidate_list(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:registered", "th-001", "environment", "switchbot", "meter", "", "登録済み"))
    manager = BleManager(registry)
    manager.scanning = True
    manager.record_advertisement(_candidate("switchbot:registered"))
    manager.record_advertisement(_candidate("switchbot:new"))
    assert [item["device_key"] for item in manager.candidate_list()] == ["switchbot:new"]
    manager.register({"device_key": "switchbot:new", "sensor_id": "th-002", "display_name": "新規"})
    assert manager.candidate_list() == []
    assert [item["sensor_id"] for item in manager.registered_list()] == ["th-001", "th-002"]


def test_new_setup_session_resets_candidate_order_and_suggests_type_sequence(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:old", "co2-001", "environment", "switchbot", "meter_pro_co2", "", "既存"))
    manager = BleManager(registry)
    manager.scanning = True
    manager.record_advertisement(_candidate("switchbot:first"))
    manager.begin_setup_session()
    manager.record_advertisement(_candidate("switchbot:co2", model="meter_pro_co2"))
    assert [item["device_key"] for item in manager.candidate_list()] == ["switchbot:co2"]
    assert manager.suggested_sensor_id("switchbot:co2") == "co2-002"
