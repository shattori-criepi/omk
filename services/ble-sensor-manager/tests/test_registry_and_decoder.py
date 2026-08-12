import asyncio
import json
from datetime import datetime, timezone
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
    item = BleManager(registry).registered_list()[0]
    assert item["status"] == "unreceived"
    assert item["latest"] is None
    assert item["online"] is False


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


def test_registered_list_joins_latest_runtime_state_without_modifying_registry(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    online_sensor = RegisteredSensor("switchbot:co2", "co2-001", "environment", "switchbot", "meter_pro_co2", "bedroom", "寝室")
    offline_sensor = RegisteredSensor("switchbot:old", "co2-002", "environment", "switchbot", "meter_pro_co2", "hall", "廊下")
    registry.register(online_sensor)
    registry.register(offline_sensor)
    # 13:55:01 is 899 seconds old (online); 13:54:59 is 901 seconds old
    # (offline). The fixed clock avoids dependence on test execution time.
    manager = BleManager(registry, now_provider=lambda: datetime(2026, 8, 12, 14, 10, tzinfo=timezone.utc))
    manager.record_advertisement(DecodedAdvertisement(
        "switchbot:co2", "switchbot", "meter_pro_co2", "environment", -42,
        "2026-08-12T22:55:01+09:00", {"temperature_c": 26.4, "relative_humidity_percent": 44, "co2_ppm": 588}, {"manufacturer_data": {"0969": "raw"}},
    ))
    manager.record_advertisement(DecodedAdvertisement(
        "switchbot:old", "switchbot", "meter_pro_co2", "environment", -80,
        "2026-08-12T22:54:59+09:00", {"co2_ppm": 500}, {},
    ))
    online, offline = manager.registered_list()
    assert online["latest"] == {"received_at": "2026-08-12T22:55:01+09:00", "rssi": -42, "values": {"temperature_c": 26.4, "relative_humidity_percent": 44, "co2_ppm": 588}}
    assert online["online"] is True
    assert online["status"] == "normal"
    assert offline["online"] is False
    assert offline["status"] == "offline"
    assert registry.list() == [online_sensor, offline_sensor]


def test_registered_latest_updates_when_setup_mode_is_stopped(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:meter", "th-001", "environment", "switchbot", "meter", "", "リビング"))
    manager = BleManager(registry)
    assert manager.scanning is False
    manager.record_advertisement(_candidate("switchbot:meter", rssi=-50, values={"temperature_c": 25.6, "relative_humidity_percent": 46}))
    first = manager.registered_list()[0]["latest"]
    manager.record_advertisement(DecodedAdvertisement("switchbot:meter", "switchbot", "meter", "environment", -30, "updated", {"temperature_c": 25.7, "relative_humidity_percent": 46}, {}))
    latest = manager.registered_list()[0]["latest"]
    assert first is not None and first["rssi"] == -50
    assert latest == {"received_at": "updated", "rssi": -30, "values": {"temperature_c": 25.7, "relative_humidity_percent": 46}}


def test_unknown_registered_model_with_latest_does_not_raise(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:unknown", "sensor-001", "unknown", "switchbot", "unknown_switchbot", "", "未知"))
    manager = BleManager(registry)
    manager.record_advertisement(_candidate("switchbot:unknown", model="unknown_switchbot"))
    assert manager.registered_list()[0]["latest"] is not None


class _MqttPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, **_kwargs: object) -> None:
        self.messages.append((topic, payload))


def test_update_registered_sensor_changes_only_logical_settings_and_keeps_runtime_state(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    original = RegisteredSensor("switchbot:co2", "co2-002", "environment", "switchbot", "meter_pro_co2", "bedroom", "寝室")
    registry.register(original)
    manager = BleManager(registry)
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 588}, model="meter_pro_co2"))
    updated = manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "主寝室", "location": "main-bedroom", "enabled": False})
    assert updated.device_key == original.device_key
    assert (updated.vendor, updated.model, updated.sensor_type) == (original.vendor, original.model, original.sensor_type)
    assert (updated.sensor_id, updated.display_name, updated.location, updated.enabled) == ("co2-001", "主寝室", "main-bedroom", False)
    assert manager.registered_list()[0]["latest"]["values"] == {"co2_ppm": 588}
    saved = json.loads((tmp_path / "sensors.json").read_text(encoding="utf-8"))
    assert set(saved["sensors"][0]) == {"device_key", "sensor_id", "sensor_type", "vendor", "model", "location", "display_name", "enabled"}


def test_update_rejects_duplicate_or_invalid_sensor_id_and_unknown_device(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:a", "co2-001", "environment", "switchbot", "meter_pro_co2", "", "A"))
    registry.register(RegisteredSensor("switchbot:b", "co2-002", "environment", "switchbot", "meter_pro_co2", "", "B"))
    manager = BleManager(registry)
    with pytest.raises(RegistryError, match="already"):
        manager.update_registered_sensor("switchbot:b", {"sensor_id": "co2-001", "display_name": "B", "location": "", "enabled": True})
    with pytest.raises(RegistryError, match="lowercase"):
        manager.update_registered_sensor("switchbot:b", {"sensor_id": "Bad_ID", "display_name": "B", "location": "", "enabled": True})
    with pytest.raises(KeyError):
        manager.update_registered_sensor("switchbot:missing", {"sensor_id": "co2-003", "display_name": "X", "location": "", "enabled": True})


def test_updated_sensor_id_is_used_for_next_mqtt_publish_and_disabled_sensor_is_not_published(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:co2", "co2-002", "environment", "switchbot", "meter_pro_co2", "", "CO2"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "CO2", "location": "", "enabled": True})
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 600}, model="meter_pro_co2"))
    assert publisher.messages[0][0] == "omk/co2-001/environment"
    assert json.loads(publisher.messages[0][1])["co2_ppm"] == 600
    manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "CO2", "location": "", "enabled": False})
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 601}, model="meter_pro_co2"))
    assert len(publisher.messages) == 1


def test_motion_manufacturer_packets_decode_only_motion_state() -> None:
    false = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150b2c0087")}, {}, "now")
    true = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150c6c0000")}, {}, "now")
    assert false is not None and false.model == "motion_sensor" and false.sensor_type == "motion"
    assert false.values == {"motion_state": 0}
    assert true is not None and true.values == {"motion_state": 1}


def test_motion_decoder_does_not_classify_existing_or_unrelated_switchbot_layouts() -> None:
    meter = decode("CF:39:41:C7:ED:79", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cf3941c7ed79f40304992c")}, {}, "now")
    co2 = decode("B0:E9:FE:58:15:CC", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fe5815ccf6e405982e0024020e00")}, {}, "now")
    malformed = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150c")}, {}, "now")
    unrelated = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150b2d0087")}, {}, "now")
    assert meter is not None and meter.model == "meter"
    assert co2 is not None and co2.model == "meter_pro_co2"
    assert malformed is not None and malformed.model == "unknown_switchbot"
    assert unrelated is not None and unrelated.model == "unknown_switchbot"


def _motion_advertisement(state: int, received_at: str = "2026-08-12T14:47:12+09:00") -> DecodedAdvertisement:
    return DecodedAdvertisement("switchbot:motion", "switchbot", "motion_sensor", "motion", -31, received_at, {"motion_state": state}, {"manufacturer_data": {"0969": "raw"}})


def test_motion_publishes_only_state_transitions_after_first_observation(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "hall", "廊下"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    for state in (0, 0, 0, 1, 1, 1, 0, 0, 1):
        manager.record_advertisement(_motion_advertisement(state))
    assert [topic for topic, _payload in publisher.messages] == ["omk/motion-001/motion", "omk/motion-001/motion", "omk/motion-001/motion"]
    assert [json.loads(payload)["motion_state"] for _topic, payload in publisher.messages] == [1, 0, 1]
    assert all(set(json.loads(payload)) == {"device_id", "measured_at", "motion_state"} for _topic, payload in publisher.messages)


def test_motion_first_true_and_disabled_state_changes_do_not_publish(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "", "人感", enabled=False))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.record_advertisement(_motion_advertisement(1))
    manager.record_advertisement(_motion_advertisement(0))
    manager.record_advertisement(_motion_advertisement(1))
    assert publisher.messages == []
    assert manager.registered_list()[0]["latest"]["values"] == {"motion_state": 1}
    assert registry.list()[0].enabled is False


def test_motion_sensor_id_change_uses_new_topic_and_setup_suggestion(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "", "人感"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.record_advertisement(_motion_advertisement(0))
    manager.update_registered_sensor("switchbot:motion", {"sensor_id": "motion-002", "display_name": "人感", "location": "", "enabled": True})
    manager.record_advertisement(_motion_advertisement(1))
    assert publisher.messages[0][0] == "omk/motion-002/motion"
    manager.scanning = True
    manager.record_advertisement(DecodedAdvertisement("switchbot:new-motion", "switchbot", "motion_sensor", "motion", -50, "now", {"motion_state": 0}, {}))
    assert manager.suggested_sensor_id("switchbot:new-motion") == "motion-001"


def test_contact_sensor_decodes_official_service_data_and_manufacturer_layouts() -> None:
    closed = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes.fromhex("64006401068f053d81")}, "now")
    opened = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x02])}, "now")
    timeout = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x04])}, "now")
    manufacturer_open = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125704c00530051c1")}, {}, "now")
    manufacturer_closed = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125715c0059000041")}, {}, "now")
    assert closed is not None and closed.model == "contact_sensor" and closed.sensor_type == "contact" and closed.values == {"contact_state": 0}
    assert opened is not None and opened.values == {"contact_state": 1}
    assert timeout is not None and timeout.values == {"contact_state": 1}
    assert manufacturer_open is not None and manufacturer_open.values == {"contact_state": 1}
    assert manufacturer_closed is not None and manufacturer_closed.values == {"contact_state": 0}


def test_contact_prefers_current_manufacturer_state_over_stale_service_data() -> None:
    stale_open_service_data = {METER_SERVICE_UUID: bytes.fromhex("64006405011300d981")}
    manufacturer_closed = {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125865c004a000041")}
    manufacturer_open = {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125854c00450035c1")}
    closed = decode("D3:B2:04:E2:31:25", -31, manufacturer_closed, stale_open_service_data, "now")
    opened = decode("D3:B2:04:E2:31:25", -31, manufacturer_open, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x01])}, "now")
    assert closed is not None and closed.model == "contact_sensor" and closed.values == {"contact_state": 0}
    assert opened is not None and opened.model == "contact_sensor" and opened.values == {"contact_state": 1}


def test_contact_decoder_does_not_misclassify_existing_sensor_layouts() -> None:
    meter = decode("CF:39:41:C7:ED:79", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cf3941c7ed79f40304992c")}, {}, "now")
    co2 = decode("B0:E9:FE:58:15:CC", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fe5815ccf6e405982e0024020e00")}, {}, "now")
    motion = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150c6c0000")}, {}, "now")
    malformed = decode("D3:B2:04:E2:31:25", -40, {}, {METER_SERVICE_UUID: bytes.fromhex("64")}, "now")
    unrelated = decode("D3:B2:04:E2:31:25", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125704c01530051c1")}, {}, "now")
    assert meter is not None and meter.model == "meter"
    assert co2 is not None and co2.model == "meter_pro_co2"
    assert motion is not None and motion.model == "motion_sensor"
    assert malformed is not None and malformed.model == "unknown_switchbot"
    assert unrelated is not None and unrelated.model == "unknown_switchbot"


def _contact_advertisement(state: int, received_at: str = "2026-08-12T14:47:12+09:00") -> DecodedAdvertisement:
    return DecodedAdvertisement("switchbot:contact", "switchbot", "contact_sensor", "contact", -31, received_at, {"contact_state": state}, {"manufacturer_data": {"0969": "raw"}})


def test_contact_publishes_only_open_closed_transitions_after_initial_state(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:contact", "contact-001", "contact", "switchbot", "contact_sensor", "door", "ドア"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    for state in (0, 0, 1, 1, 0, 0, 1):
        manager.record_advertisement(_contact_advertisement(state))
    assert [topic for topic, _payload in publisher.messages] == ["omk/contact-001/contact"] * 3
    assert [json.loads(payload)["contact_state"] for _topic, payload in publisher.messages] == [1, 0, 1]


def test_contact_disabled_new_sensor_id_latest_and_setup_suggestion(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:contact", "contact-001", "contact", "switchbot", "contact_sensor", "", "ドア", enabled=False))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.record_advertisement(_contact_advertisement(1))
    manager.record_advertisement(_contact_advertisement(0))
    assert publisher.messages == []
    assert manager.registered_list()[0]["latest"]["values"] == {"contact_state": 0}
    manager.update_registered_sensor("switchbot:contact", {"sensor_id": "contact-002", "display_name": "ドア", "location": "", "enabled": True})
    manager.record_advertisement(_contact_advertisement(1))
    assert publisher.messages[0][0] == "omk/contact-002/contact"
    manager.scanning = True
    manager.record_advertisement(DecodedAdvertisement("switchbot:new-contact", "switchbot", "contact_sensor", "contact", -50, "now", {"contact_state": 0}, {}))
    assert manager.suggested_sensor_id("switchbot:new-contact") == "contact-001"
