import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from omk_ble import main as ble_main
from omk_ble.registry import RegistryError, SensorRegistry
from omk_ble.node_registry import NodeRegistry
from omk_ble.models import DecodedAdvertisement, RegisteredSensor
from omk_ble.omk_node import OMK_NODE_SERVICE_UUID, decode as decode_omk_node
from omk_ble.switchbot import METER_SERVICE_UUID, SWITCHBOT_COMPANY_ID, decode
from omk_ble.service import BleManager


def test_meter_advertisement_decodes_documented_environment_fields() -> None:
    decoded = decode(
        "AA:BB:CC:DD:EE:01", -43, {SWITCHBOT_COMPANY_ID: b"\x01"},
        {METER_SERVICE_UUID: bytes([0x54, 0, 87, 0x03, 0x99, 52])},
        "2026-08-12T10:00:00+09:00",
    )
    assert decoded is not None
    assert decoded.model == "temperature_humidity_sensor"
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


def test_omk_node_provisioning_advertisement_is_decoded_without_secrets() -> None:
    decoded = decode_omk_node(
        -41,
        {OMK_NODE_SERVICE_UUID: bytes.fromhex("01000001112233445566")},
        "2026-08-14T10:00:00+09:00",
    )
    assert decoded is not None
    assert decoded.device_key == "omk-node:112233445566"
    assert decoded.rssi == -41
    assert decoded.values == {
        "protocol_version": 1,
        "node_id": "112233445566",
        "capabilities": ["ble_scan"],
        "provisioning_state": "unregistered",
    }
    assert "site_uuid" not in str(decoded.as_dict())


@pytest.mark.parametrize(
    "service_data",
    [
        {},
        {OMK_NODE_SERVICE_UUID: b"\x01"},
        {OMK_NODE_SERVICE_UUID: bytes.fromhex("02000001112233445566")},
        {OMK_NODE_SERVICE_UUID: bytes.fromhex("01008000112233445566")},
        {OMK_NODE_SERVICE_UUID: bytes.fromhex("01000001000000000000")},
    ],
)
def test_invalid_or_unrelated_omk_node_advertisements_are_ignored(service_data: dict[str, bytes]) -> None:
    assert decode_omk_node(-50, service_data, "now") is None


def test_omk_node_provisioned_and_registered_states_decode() -> None:
    for state, expected in ((1, "provisioned"), (2, "registered")):
        packet = bytes([1, state, 0, 1, 1, 2, 3, 4, 5, 6])
        decoded = decode_omk_node(-50, {OMK_NODE_SERVICE_UUID: packet}, "now")
        assert decoded is not None and decoded.values["provisioning_state"] == expected


def test_omk_node_candidate_cannot_be_formally_registered_yet(tmp_path: Path) -> None:
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    decoded = decode_omk_node(-41, {OMK_NODE_SERVICE_UUID: bytes.fromhex("01000002112233445566")}, "now")
    assert decoded is not None
    manager.record_advertisement(decoded)
    candidate = manager.candidate_list()[0]
    assert candidate["values"]["capabilities"] == ["sen66"]
    with pytest.raises(ValueError, match="まだ実装されていません"):
        manager.register({"device_key": decoded.device_key, "sensor_id": "node-001", "display_name": "Node"})


def test_ble_scan_callback_adds_omk_node_candidate_without_affecting_switchbot_decoder(tmp_path: Path) -> None:
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    device = type("Device", (), {"address": "AA:BB:CC:DD:EE:FF"})()
    advertisement = type(
        "Advertisement",
        (),
        {
            "rssi": -41,
            "manufacturer_data": {},
            "service_data": {OMK_NODE_SERVICE_UUID.upper(): bytes.fromhex("01000001112233445566")},
        },
    )()
    manager._on_detection(device, advertisement)
    assert manager.candidate_list()[0]["device_key"] == "omk-node:112233445566"


def test_pi_captured_meter_manufacturer_packets_decode_temperature_and_humidity() -> None:
    fixtures = (("cf3941c7ed79f40304992c", 25.4), ("cf3941c7ed79f80305992c", 25.5))
    for packet, temperature_c in fixtures:
        decoded = decode(
            "CF:39:41:C7:ED:79", -28,
            {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "2026-08-12T10:00:00+09:00",
        )
        assert decoded is not None
        assert decoded.device_key == "switchbot:cf3941c7ed79"
        assert decoded.model == "temperature_humidity_sensor"
        assert decoded.sensor_type == "environment"
        assert decoded.values == {"temperature_c": temperature_c, "relative_humidity_percent": 44}


def test_pi_captured_co2_manufacturer_packets_decode_environment_measurements() -> None:
    fixtures = (
        ("B0:E9:FE:54:96:C5", "b0e9fe5496c5b6e4059a2a003f02ad00", {"co2_ppm": 685}),
        ("B0:E9:FE:54:96:C5", "b0e9fe5496c5b9e4059a28003f028100", {"co2_ppm": 641}),
        ("B0:E9:FE:58:15:CC", "b0e9fe5815ccf6e405982e0024020e00", {"temperature_c": 24.5, "relative_humidity_percent": 46, "co2_ppm": 526}),
        ("B0:E9:FE:58:15:CC", "b0e9fe5815ccf7e406982e0024027b00", {"temperature_c": 24.6, "relative_humidity_percent": 46, "co2_ppm": 635}),
        ("B0:E9:FE:54:96:C5", "b0e9fe5496c50ae4029c250334022600", {"temperature_c": 28.2, "relative_humidity_percent": 37, "co2_ppm": 550}),
        ("B0:E9:FE:58:15:CC", "b0e9fe5815cc788006992a002d027000", {"temperature_c": 25.6, "relative_humidity_percent": 42, "co2_ppm": 624}),
    )
    for address, packet, expected in fixtures:
        decoded = decode(address, -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now")
        assert decoded is not None
        assert decoded.model == "co2_sensor"
        assert decoded.sensor_type == "environment"
        for key, value in expected.items():
            assert decoded.values[key] == value


@pytest.mark.parametrize("packet", (
    "b0e9fe5496c50ae4029c2503340226",  # len != 16
    "b0e9fe5496c50ae40a9c250334022600",  # invalid temperature fraction
    "b0e9fe5496c50ae4029c650334022600",  # invalid humidity
    "b0e9fe5496c50ae4029c250334018f00",  # CO2 < 400 ppm
    "b0e9fe5496c50ae4029c250334271100",  # CO2 > 10,000 ppm
    "b0e9fe5496c50ae4029c250334022601",  # non-zero terminator
))
def test_invalid_co2_manufacturer_packets_remain_unknown(packet: str) -> None:
    decoded = decode(
        "B0:E9:FE:54:96:C5", -31,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now",
    )
    assert decoded is not None
    assert decoded.model == "unknown_switchbot"
    assert decoded.values == {}


def test_waterproof_sensor_decodes_its_dedicated_service_and_manufacturer_layouts() -> None:
    service_only = decode(
        "D6:69:17:D3:10:38", -53, {}, {METER_SERVICE_UUID: bytes.fromhex("770047")}, "now",
    )
    captured = decode(
        "D6:69:17:D3:10:38", -53,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("d66917d31038550b069bd200")},
        {METER_SERVICE_UUID: bytes.fromhex("770047")}, "now",
    )
    below_freezing = decode(
        "11:22:33:44:55:66", -60,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("112233445566550b06055200")}, {}, "now",
    )
    malformed = decode("x", -1, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d66917d31038550b069bd2")}, {}, "now")
    unrelated = decode("x", -1, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d66917d31038550b069bd201")}, {}, "now")
    assert service_only is not None and service_only.model == "waterproof_sensor" and service_only.values == {}
    assert captured is not None and captured.sensor_type == "environment"
    assert captured.values == {"temperature_c": 27.6, "relative_humidity_percent": 82}
    assert "humidity_percent" not in captured.values
    assert below_freezing is not None and below_freezing.values == {"temperature_c": -5.6, "relative_humidity_percent": 82}
    assert malformed is not None and malformed.model == "unknown_switchbot"
    assert unrelated is not None and unrelated.model == "unknown_switchbot"


def test_plug_sensor_decodes_service_and_manufacturer_power_layouts() -> None:
    service_only = decode("60:55:F9:2E:77:82", -50, {}, {METER_SERVICE_UUID: bytes.fromhex("6a0064")}, "now")
    on_low = decode("60:55:F9:2E:77:82", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e7782378016360035")}, {}, "now")
    off_low = decode("60:55:F9:2E:77:82", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e7782380016360035")}, {}, "now")
    on_high = decode("60:55:F9:2E:77:82", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e77827080163806c4")}, {}, "now")
    overload_high = decode("60:55:F9:2E:77:82", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e77827080163886c4")}, {}, "now")
    unrelated = decode("x", -1, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e7782378015360035")}, {}, "now")
    old_verified = decode("60:55:F9:2E:77:82", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex("6055f92e77826f80163805f2")}, {}, "now")
    new_verified = decode(
        "AC:27:6E:43:26:9E", -50,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("ac276e43269e778010370037")},
        {METER_SERVICE_UUID: bytes.fromhex("6a0064")}, "now",
    )
    assert service_only is not None and service_only.model == "plug_sensor" and service_only.sensor_type == "power" and service_only.values == {}
    assert on_low is not None and on_low.device_key == "switchbot:6055f92e7782"
    assert on_low.values == {"power_w": 5.3, "switch_state": 1}
    assert off_low is not None and off_low.values == {"power_w": 5.3, "switch_state": 0}
    assert on_high is not None and on_high.values == {"power_w": 173.2, "switch_state": 1}
    assert overload_high is not None and overload_high.values == {"power_w": 173.2, "switch_state": 1}
    assert unrelated is not None and unrelated.model == "unknown_switchbot"
    assert old_verified is not None and old_verified.values == {"power_w": 152.2, "switch_state": 1}
    assert new_verified is not None and new_verified.model == "plug_sensor" and new_verified.sensor_type == "power"
    assert new_verified.values == {"power_w": 5.5, "switch_state": 1}


def test_registered_plug_model_hint_decodes_ambiguous_manufacturer_only_packets() -> None:
    packets = (
        ("ac276e43269e96801032002a", {"switch_state": 1, "power_w": 4.2}),
        ("ac276e43269e9c0010330029", {"switch_state": 0, "power_w": 4.1}),
        ("ac276e43269e9d0010330000", {"switch_state": 0, "power_w": 0.0}),
    )
    for packet, values in packets:
        hinted = decode("AC:27:6E:43:26:9E", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now", model_hint="plug_sensor")
        assert hinted is not None and hinted.model == "plug_sensor" and hinted.values == values

    # Without registration metadata this collision remains conservatively
    # non-Plug; the last packet can otherwise match Waterproof's 12-byte form.
    unhinted = decode("AC:27:6E:43:26:9E", -50, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packets[-1][0])}, {}, "now")
    assert unhinted is not None and unhinted.model != "plug_sensor"


def test_short_and_unknown_manufacturer_packets_remain_raw_unknown_candidates() -> None:
    for packet in (b"", bytes.fromhex("cf3941c7ed79f40304"), bytes.fromhex("cf3941c7ed79000004992c")):
        decoded = decode("CF:39:41:C7:ED:79", -50, {SWITCHBOT_COMPANY_ID: packet}, {}, "now")
        assert decoded is not None
        assert decoded.model == "unknown_switchbot"
        assert decoded.values == {}


def test_registry_validates_ids_and_prevents_duplicate_device_or_id(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    one = RegisteredSensor("switchbot:abc", "living-env-01", "environment", "switchbot", "temperature_humidity_sensor", "living", "リビング")
    registry.register(one)
    assert registry.list() == [one]
    with pytest.raises(RegistryError, match="already"):
        registry.register(one)
    with pytest.raises(RegistryError, match="lowercase"):
        registry.register(RegisteredSensor("switchbot:def", "Bad_ID", "environment", "switchbot", "temperature_humidity_sensor", "", "x"))


def test_registry_delete_releases_sensor_id_and_preserves_other_registrations(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    old = RegisteredSensor("switchbot:old", "plug-001", "power", "switchbot", "plug_sensor", "", "旧プラグ")
    other = RegisteredSensor("switchbot:other", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "温湿度計")
    registry.register(old)
    registry.register(other)

    assert registry.delete("switchbot:old") == old
    assert registry.list() == [other]
    assert [item["sensor_id"] for item in json.loads((tmp_path / "sensors.json").read_text(encoding="utf-8"))["sensors"]] == ["th-001"]
    replacement = RegisteredSensor("switchbot:new", "plug-001", "power", "switchbot", "plug_sensor", "", "新品プラグ")
    assert registry.register(replacement) == replacement
    with pytest.raises(KeyError):
        registry.delete("switchbot:missing")


def test_deleting_registered_sensor_stops_publish_and_allows_setup_candidate(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    sensor = RegisteredSensor("switchbot:plug", "plug-001", "power", "switchbot", "plug_sensor", "", "プラグ")
    registry.register(sensor)
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    advertisement = DecodedAdvertisement("switchbot:plug", "switchbot", "plug_sensor", "power", -45, "now", {"power_w": 5.2, "switch_state": 1})
    manager.record_advertisement(advertisement)
    assert len(publisher.messages) == 1

    assert manager.delete_registered_sensor("switchbot:plug") == sensor
    assert manager.registered_list() == []
    manager.scanning = True
    manager.record_advertisement(advertisement)
    assert len(publisher.messages) == 1
    assert [item["device_key"] for item in manager.candidate_list()] == ["switchbot:plug"]
    assert manager.suggested_sensor_id("switchbot:plug") == "plug-001"


def test_delete_sensor_api_returns_deleted_identity_and_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:plug", "plug-001", "power", "switchbot", "plug_sensor", "", "プラグ"))
    monkeypatch.setattr(ble_main, "manager", BleManager(registry))

    assert ble_main.delete_sensor("switchbot:plug") == {"deleted": True, "device_key": "switchbot:plug", "sensor_id": "plug-001"}
    with pytest.raises(HTTPException) as error:
        ble_main.delete_sensor("switchbot:missing")
    assert error.value.status_code == 404


def test_registry_normalizes_legacy_switchbot_models_when_read_and_written(tmp_path: Path) -> None:
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"sensors": [
        {"device_key": "switchbot:meter", "sensor_id": "th-001", "sensor_type": "environment", "vendor": "switchbot", "model": "meter", "location": "", "display_name": "温湿度計", "enabled": True},
        {"device_key": "switchbot:co2", "sensor_id": "co2-001", "sensor_type": "environment", "vendor": "switchbot", "model": "meter_pro_co2", "location": "", "display_name": "CO2", "enabled": True},
    ]}), encoding="utf-8")
    registry = SensorRegistry(path)
    assert [sensor.model for sensor in registry.list()] == ["temperature_humidity_sensor", "co2_sensor"]
    registry.update("switchbot:meter", sensor_id="th-001", display_name="温湿度計", location="室内", enabled=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [sensor["model"] for sensor in saved["sensors"]] == ["temperature_humidity_sensor", "co2_sensor"]


def test_offline_registration_is_kept_when_no_advertisement_is_seen(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:abc", "living-env-01", "environment", "switchbot", "temperature_humidity_sensor", "", "リビング"))
    item = BleManager(registry).registered_list()[0]
    assert item["status"] == "unreceived"
    assert item["latest"] is None
    assert item["online"] is False


def _candidate(device_key: str, *, rssi: int = -50, values: dict | None = None, model: str = "temperature_humidity_sensor") -> DecodedAdvertisement:
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
    first = DecodedAdvertisement("switchbot:a", "switchbot", "co2_sensor", "environment", -79, "first", {"co2_ppm": 577}, {"manufacturer_data": {"0969": "first"}})
    second = DecodedAdvertisement("switchbot:a", "switchbot", "co2_sensor", "environment", -31, "second", {"co2_ppm": 566}, {"manufacturer_data": {"0969": "second"}})
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
    monkeypatch.setattr("omk_ble.service.decode", lambda *_args, **_kwargs: next(decoded))
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
    registry.register(RegisteredSensor("switchbot:registered", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "登録済み"))
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
    registry.register(RegisteredSensor("switchbot:old", "co2-001", "environment", "switchbot", "co2_sensor", "", "既存"))
    manager = BleManager(registry)
    manager.scanning = True
    manager.record_advertisement(_candidate("switchbot:first"))
    manager.begin_setup_session()
    manager.record_advertisement(_candidate("switchbot:co2", model="co2_sensor"))
    assert [item["device_key"] for item in manager.candidate_list()] == ["switchbot:co2"]
    assert manager.suggested_sensor_id("switchbot:co2") == "co2-002"


def test_registered_list_joins_latest_runtime_state_without_modifying_registry(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    online_sensor = RegisteredSensor("switchbot:co2", "co2-001", "environment", "switchbot", "co2_sensor", "bedroom", "寝室")
    offline_sensor = RegisteredSensor("switchbot:old", "co2-002", "environment", "switchbot", "co2_sensor", "hall", "廊下")
    registry.register(online_sensor)
    registry.register(offline_sensor)
    # 13:55:01 is 899 seconds old (online); 13:54:59 is 901 seconds old
    # (offline). The fixed clock avoids dependence on test execution time.
    manager = BleManager(registry, now_provider=lambda: datetime(2026, 8, 12, 14, 10, tzinfo=timezone.utc))
    manager.record_advertisement(DecodedAdvertisement(
        "switchbot:co2", "switchbot", "co2_sensor", "environment", -42,
        "2026-08-12T22:55:01+09:00", {"temperature_c": 26.4, "relative_humidity_percent": 44, "co2_ppm": 588}, {"manufacturer_data": {"0969": "raw"}},
    ))
    manager.record_advertisement(DecodedAdvertisement(
        "switchbot:old", "switchbot", "co2_sensor", "environment", -80,
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
    registry.register(RegisteredSensor("switchbot:meter", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "リビング"))
    manager = BleManager(registry)
    assert manager.scanning is False
    manager.record_advertisement(_candidate("switchbot:meter", rssi=-50, values={"temperature_c": 25.6, "relative_humidity_percent": 46}))
    first = manager.registered_list()[0]["latest"]
    manager.record_advertisement(DecodedAdvertisement("switchbot:meter", "switchbot", "temperature_humidity_sensor", "environment", -30, "updated", {"temperature_c": 25.7, "relative_humidity_percent": 46}, {}))
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


def test_node_registration_request_and_ack_are_persisted(tmp_path: Path) -> None:
    publisher = _MqttPublisher()
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher, node_registry=node_registry)
    decoded = decode_omk_node(-41, {OMK_NODE_SERVICE_UUID: bytes.fromhex("01010001112233445566")}, "now")
    assert decoded is not None
    manager.record_advertisement(decoded)

    assert manager.request_node_registration("112233445566", "ble-relay-001") == {
        "node_id": "112233445566", "logical_id": "ble-relay-001", "status": "request_sent",
    }
    assert publisher.messages == [("omk/node/112233445566/registration/config", '{"protocol_version": 1, "logical_id": "ble-relay-001"}')]
    manager.handle_node_mqtt("omk/node/112233445566/registration/ack", b'{"protocol_version":1,"node_id":"112233445566","logical_id":"ble-relay-001","registration_state":"registered"}')
    assert manager.node_list()[0]["registration_state"] == "registered"
    assert manager.request_node_registration("112233445566", "sen66-002")["status"] == "request_sent"
    assert node_registry.list()["112233445566"]["logical_id"] == "ble-relay-001"
    manager.handle_node_mqtt("omk/node/112233445566/registration/ack", b'{"protocol_version":1,"node_id":"112233445566","logical_id":"sen66-002","registration_state":"registered"}')
    assert node_registry.list()["112233445566"]["logical_id"] == "sen66-002"


def test_node_registration_removal_preserves_wifi_metadata_and_ignores_old_ack(tmp_path: Path) -> None:
    publisher = _MqttPublisher()
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    node_registry.update("112233445566", logical_id="sen66-001", registration_state="registered", connected_sensors=["sen66"])
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher, node_registry=node_registry)

    assert manager.remove_node_registration("112233445566") == {"node_id": "112233445566", "status": "removed"}
    saved = node_registry.list()["112233445566"]
    assert saved["registration_state"] == "provisioned"
    assert "logical_id" not in saved
    assert saved["registration_revoked"] is True
    assert json.loads(publisher.messages[0][1]) == {"protocol_version": 1, "logical_id": None}
    manager.handle_node_mqtt("omk/node/112233445566/registration/ack", b'{"protocol_version":1,"node_id":"112233445566","logical_id":"sen66-001","registration_state":"registered"}')
    assert "logical_id" not in node_registry.list()["112233445566"]


def test_sen66_telemetry_marks_only_the_registered_node_as_physically_connected(tmp_path: Path) -> None:
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    node_registry.update("112233445566", logical_id="sen66-001", registration_state="registered", connected_sensors=["sen66"])
    node_registry.update("9af9509eb8b6", registration_state="provisioned")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), node_registry=node_registry)
    manager.handle_sen66_mqtt("omk/sen66-001/sen66", b'{"device_id":"sen66-001"}')
    nodes = {node["node_id"]: node for node in manager.node_list()}
    assert nodes["112233445566"]["attached_sensors"] == ["SEN66"]
    assert nodes["9af9509eb8b6"]["attached_sensors"] == []


def test_node_registration_rejects_duplicate_logical_id_in_requests_and_acks(tmp_path: Path) -> None:
    publisher = _MqttPublisher()
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), publisher, node_registry=node_registry)
    first = decode_omk_node(-41, {OMK_NODE_SERVICE_UUID: bytes.fromhex("01010001112233445566")}, "now")
    second = decode_omk_node(-42, {OMK_NODE_SERVICE_UUID: bytes.fromhex("010100019af9509eb8b6")}, "now")
    assert first is not None and second is not None
    manager.record_advertisement(first)
    manager.record_advertisement(second)

    manager.request_node_registration("112233445566", "sen66-001")
    with pytest.raises(ValueError, match="already assigned to node 112233445566"):
        manager.request_node_registration("9af9509eb8b6", "sen66-001")

    manager.handle_node_mqtt("omk/node/112233445566/registration/ack", b'{"protocol_version":1,"node_id":"112233445566","logical_id":"sen66-001","registration_state":"registered"}')
    manager.handle_node_mqtt("omk/node/9af9509eb8b6/registration/ack", b'{"protocol_version":1,"node_id":"9af9509eb8b6","logical_id":"sen66-001","registration_state":"registered"}')
    saved = node_registry.list()
    assert saved["112233445566"]["logical_id"] == "sen66-001"
    assert "9af9509eb8b6" not in saved


def test_provisioned_node_status_clears_only_its_gateway_registration_metadata(tmp_path: Path) -> None:
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    node_registry.update("9af9509eb8b6", logical_id="sen66-001", request_state="registered",
                         ack_seen_at="before", unrelated="preserved")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), node_registry=node_registry)

    manager.handle_node_mqtt("omk/node/9af9509eb8b6/registration/status", b'{"protocol_version":1,"node_id":"9af9509eb8b6","registration_state":"provisioned","capabilities":1}')

    saved = node_registry.list()["9af9509eb8b6"]
    assert saved["registration_state"] == "provisioned"
    assert saved["capabilities"] == 1
    assert saved["unrelated"] == "preserved"
    assert {"logical_id", "request_state", "ack_seen_at"}.isdisjoint(saved)


def test_node_mqtt_status_validation_does_not_persist_invalid_messages(tmp_path: Path) -> None:
    node_registry = NodeRegistry(tmp_path / "nodes.json")
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"), node_registry=node_registry)
    manager.handle_node_mqtt("omk/node/112233445566/registration/status", b'{"protocol_version":2,"node_id":"112233445566","registration_state":"registered","capabilities":1}')
    manager.handle_node_mqtt("omk/node/112233445566/registration/status", b'{"protocol_version":1,"node_id":"112233445566","registration_state":"registered","capabilities":1}')
    node = manager.node_list()[0]
    assert node["node_id"] == "112233445566"
    assert node["capabilities"] == ["ble_scan"]
    assert node["registration_state"] == "registered"
    assert node["online"] is True
    assert node["attached_sensors"] == []


def test_node_registration_api_sends_a_pending_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = _MqttPublisher()
    manager = BleManager(
        SensorRegistry(tmp_path / "sensors.json"), publisher,
        node_registry=NodeRegistry(tmp_path / "nodes.json"),
    )
    decoded = decode_omk_node(-41, {OMK_NODE_SERVICE_UUID: bytes.fromhex("01010001112233445566")}, "now")
    assert decoded is not None
    manager.record_advertisement(decoded)
    monkeypatch.setattr(ble_main, "manager", manager)

    response = ble_main.register_node("112233445566", ble_main.NodeRegistrationRequest(logical_id="ble-relay-001"))
    assert response["status"] == "request_sent"
    assert publisher.messages[0][0] == "omk/node/112233445566/registration/config"


def test_update_registered_sensor_changes_only_logical_settings_and_keeps_runtime_state(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    original = RegisteredSensor("switchbot:co2", "co2-002", "environment", "switchbot", "co2_sensor", "bedroom", "寝室")
    registry.register(original)
    manager = BleManager(registry)
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 588}, model="co2_sensor"))
    updated = manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "主寝室", "location": "main-bedroom", "enabled": False})
    assert updated.device_key == original.device_key
    assert (updated.vendor, updated.model, updated.sensor_type) == (original.vendor, original.model, original.sensor_type)
    assert (updated.sensor_id, updated.display_name, updated.location, updated.enabled) == ("co2-001", "主寝室", "main-bedroom", False)
    assert manager.registered_list()[0]["latest"]["values"] == {"co2_ppm": 588}
    saved = json.loads((tmp_path / "sensors.json").read_text(encoding="utf-8"))
    assert set(saved["sensors"][0]) == {"device_key", "sensor_id", "sensor_type", "vendor", "model", "location", "display_name", "enabled"}


def test_update_rejects_duplicate_or_invalid_sensor_id_and_unknown_device(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:a", "co2-001", "environment", "switchbot", "co2_sensor", "", "A"))
    registry.register(RegisteredSensor("switchbot:b", "co2-002", "environment", "switchbot", "co2_sensor", "", "B"))
    manager = BleManager(registry)
    with pytest.raises(RegistryError, match="already"):
        manager.update_registered_sensor("switchbot:b", {"sensor_id": "co2-001", "display_name": "B", "location": "", "enabled": True})
    with pytest.raises(RegistryError, match="lowercase"):
        manager.update_registered_sensor("switchbot:b", {"sensor_id": "Bad_ID", "display_name": "B", "location": "", "enabled": True})
    with pytest.raises(KeyError):
        manager.update_registered_sensor("switchbot:missing", {"sensor_id": "co2-003", "display_name": "X", "location": "", "enabled": True})


def test_updated_sensor_id_is_used_for_next_mqtt_publish_and_disabled_sensor_is_not_published(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:co2", "co2-002", "environment", "switchbot", "co2_sensor", "", "CO2"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "CO2", "location": "", "enabled": True})
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 600}, model="co2_sensor"))
    assert publisher.messages[0][0] == "omk/co2-001/environment"
    assert json.loads(publisher.messages[0][1])["co2_ppm"] == 600
    manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "CO2", "location": "", "enabled": False})
    manager.record_advertisement(_candidate("switchbot:co2", values={"co2_ppm": 601}, model="co2_sensor"))
    assert len(publisher.messages) == 1


def test_raw_relay_reuses_switchbot_decoder_and_resolves_registered_sensor_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor(
        "switchbot:cf3941c7ed79", "th-001", "environment", "switchbot",
        "temperature_humidity_sensor", "", "温湿度計",
    ))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    monkeypatch.setattr("omk_ble.service.now_iso", lambda: "2026-08-20T10:00:00+00:00")

    manager.handle_relay_mqtt(
        "omk-relay/09dda0d5a8f2/ble/raw",
        b'{"protocol_version":1,"relay_node_id":"09dda0d5a8f2","ble_address":"cf3941c7ed79","rssi":-42,'
        b'"manufacturer_data":[{"company_id":2409,"data":"cf3941c7ed79f40304992c"}],"service_data":[]}',
    )

    assert publisher.messages == [("omk/th-001/environment", json.dumps({
        "device_id": "th-001", "measured_at": "2026-08-20T10:00:00+00:00", "quality": "normal",
        "temperature_c": 25.4, "relative_humidity_percent": 44,
        "source": "relay", "relay_node_id": "09dda0d5a8f2",
    }))]


def test_raw_relay_does_not_publish_unregistered_or_disabled_devices(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor(
        "switchbot:111111111111", "th-002", "environment", "switchbot",
        "temperature_humidity_sensor", "", "無効", enabled=False,
    ))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)

    def relay(address: str) -> None:
        manager.handle_relay_mqtt(
            "omk-relay/09dda0d5a8f2/ble/raw",
            json.dumps({
                "protocol_version": 1, "relay_node_id": "09dda0d5a8f2", "ble_address": address, "rssi": -42,
                "manufacturer_data": [{"company_id": 2409, "data": address + "f40304992c"}], "service_data": [],
            }).encode(),
        )

    relay("cf3941c7ed79")
    relay("111111111111")
    assert publisher.messages == []


def test_registered_plug_model_hint_is_used_for_direct_and_raw_relay(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:ac276e43269e", "plug-001", "power", "switchbot", "plug_sensor", "", "プラグ"))
    packet = "ac276e43269e9d0010330000"
    direct_publisher = _MqttPublisher()
    direct_manager = BleManager(registry, direct_publisher)
    device = type("Device", (), {"address": "AC:27:6E:43:26:9E"})()
    advertisement = type("Advertisement", (), {"rssi": -50, "manufacturer_data": {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, "service_data": {}})()
    direct_manager._on_detection(device, advertisement)
    assert json.loads(direct_publisher.messages[-1][1])["power_w"] == 0.0

    relay_publisher = _MqttPublisher()
    relay_manager = BleManager(registry, relay_publisher)
    relay_manager.handle_relay_mqtt(
        "omk-relay/09dda0d5a8f2/ble/raw",
        json.dumps({"protocol_version": 1, "relay_node_id": "09dda0d5a8f2", "ble_address": "ac276e43269e", "rssi": -50,
                    "manufacturer_data": [{"company_id": 2409, "data": packet}], "service_data": []}).encode(),
    )
    relay_payload = json.loads(relay_publisher.messages[-1][1])
    assert relay_payload["source"] == "relay" and relay_payload["power_w"] == 0.0


def test_environment_direct_route_suppresses_relay_then_falls_back_and_recovers(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:meter", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "温湿度計"))
    clock = [0.0]
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])

    def observation(value: float, received_at: str) -> DecodedAdvertisement:
        return DecodedAdvertisement("switchbot:meter", "switchbot", "temperature_humidity_sensor", "environment", -40, received_at, {"temperature_c": value, "relative_humidity_percent": 50}, {})

    manager.record_advertisement(observation(20.0, "direct-first"))
    manager.record_advertisement(observation(30.0, "relay-fresh"), source="relay", relay_node_id="09dda0d5a8f2")
    clock[0] = 1.0
    manager.record_advertisement(observation(20.0, "direct-rate-limited"))
    clock[0] = 30.9
    manager.record_advertisement(observation(30.0, "relay-29.9"), source="relay", relay_node_id="09dda0d5a8f2")
    assert [json.loads(payload)["source"] for _, payload in publisher.messages] == ["direct"]

    clock[0] = 31.0
    manager.record_advertisement(observation(30.0, "relay-fallback"), source="relay", relay_node_id="09dda0d5a8f2")
    assert json.loads(publisher.messages[-1][1])["source"] == "relay"
    clock[0] = 31.1
    manager.record_advertisement(observation(30.0, "direct-recovered"))
    assert json.loads(publisher.messages[-1][1])["source"] == "direct"
    assert json.loads(publisher.messages[-1][1])["temperature_c"] == 30.0
    clock[0] = 31.2
    manager.record_advertisement(observation(99.0, "relay-after-recovery"), source="relay", relay_node_id="09dda0d5a8f2")
    assert len(publisher.messages) == 3


def test_contact_direct_recovery_forces_same_state_source_switch(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:contact", "contact-001", "contact", "switchbot", "contact_sensor", "", "ドア"))
    clock = [0.0]
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])

    def observation(received_at: str) -> DecodedAdvertisement:
        return DecodedAdvertisement("switchbot:contact", "switchbot", "contact_sensor", "contact", -40, received_at, {"contact_state": 1}, {})

    manager.record_advertisement(observation("direct-first"))
    clock[0] = 30.0
    manager.record_advertisement(observation("relay-fallback"), source="relay", relay_node_id="09dda0d5a8f2")
    clock[0] = 30.1
    manager.record_advertisement(observation("direct-recovered"))
    assert [json.loads(payload)["source"] for _, payload in publisher.messages] == ["direct", "relay", "direct"]


def test_environment_publish_is_rate_limited_per_device_with_latest_values(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:th", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "温湿度計"))
    registry.register(RegisteredSensor("switchbot:co2", "co2-001", "environment", "switchbot", "co2_sensor", "", "CO2"))
    registry.register(RegisteredSensor("switchbot:waterproof", "th-002", "environment", "switchbot", "waterproof_sensor", "", "防水温湿度計"))
    clock = [0.0]
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])

    def advertisement(device_key: str, model: str, received_at: str, value: int) -> DecodedAdvertisement:
        values = {"co2_ppm": value} if model == "co2_sensor" else {"temperature_c": value / 10, "relative_humidity_percent": value % 100}
        return DecodedAdvertisement(device_key, "switchbot", model, "environment", -50, received_at, values, {"raw": received_at})

    manager.record_advertisement(advertisement("switchbot:th", "temperature_humidity_sensor", "first", 200))
    manager.record_advertisement(advertisement("switchbot:co2", "co2_sensor", "first", 600))
    manager.record_advertisement(advertisement("switchbot:waterproof", "waterproof_sensor", "first", 80))
    assert len(publisher.messages) == 3
    clock[0] = 2.0
    manager.record_advertisement(advertisement("switchbot:th", "temperature_humidity_sensor", "two-seconds", 210))
    manager.record_advertisement(advertisement("switchbot:co2", "co2_sensor", "two-seconds", 601))
    manager.record_advertisement(advertisement("switchbot:waterproof", "waterproof_sensor", "two-seconds", 81))
    clock[0] = 9.0
    manager.record_advertisement(advertisement("switchbot:th", "temperature_humidity_sensor", "nine-seconds", 220))
    assert len(publisher.messages) == 3
    assert manager.registered_list()[0]["latest"] == {"received_at": "nine-seconds", "rssi": -50, "values": {"temperature_c": 22.0, "relative_humidity_percent": 20}}
    clock[0] = 10.0
    manager.record_advertisement(advertisement("switchbot:th", "temperature_humidity_sensor", "ten-seconds", 230))
    assert len(publisher.messages) == 4
    assert json.loads(publisher.messages[-1][1])["temperature_c"] == 23.0
    manager.update_registered_sensor("switchbot:th", {"sensor_id": "th-003", "display_name": "温湿度計", "location": "", "enabled": True})
    clock[0] = 20.0
    manager.record_advertisement(advertisement("switchbot:th", "temperature_humidity_sensor", "renamed", 240))
    assert publisher.messages[-1][0] == "omk/th-003/environment"
    manager.update_registered_sensor("switchbot:co2", {"sensor_id": "co2-001", "display_name": "CO2", "location": "", "enabled": False})
    clock[0] = 11.0
    manager.record_advertisement(advertisement("switchbot:co2", "co2_sensor", "disabled", 601))
    assert len(publisher.messages) == 5
    assert manager.registered_list()[1]["latest"]["values"] == {"co2_ppm": 601}


def test_plug_power_publishes_periodically_and_switch_changes_immediately(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:plug", "plug-001", "power", "switchbot", "plug_sensor", "", "プラグ"))
    registry.register(RegisteredSensor("switchbot:disabled-plug", "plug-002", "power", "switchbot", "plug_sensor", "", "無効", enabled=False))
    clock = [0.0]
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])

    def advertisement(device_key: str, state: int, watts: float, received_at: str) -> DecodedAdvertisement:
        return DecodedAdvertisement(device_key, "switchbot", "plug_sensor", "power", -45, received_at, {"power_w": watts, "switch_state": state}, {})

    manager.record_advertisement(advertisement("switchbot:plug", 1, 5.3, "first"))
    clock[0] = 2.0
    manager.record_advertisement(advertisement("switchbot:plug", 1, 5.4, "two"))
    assert len(publisher.messages) == 1
    assert manager.registered_list()[0]["latest"]["values"] == {"power_w": 5.4, "switch_state": 1}
    manager.record_advertisement(advertisement("switchbot:disabled-plug", 0, 0.0, "disabled"))
    assert len(publisher.messages) == 1 and manager.registered_list()[1]["latest"] is not None
    clock[0] = 3.0
    manager.record_advertisement(advertisement("switchbot:plug", 0, 0.0, "changed"))
    assert json.loads(publisher.messages[-1][1])["switch_state"] == 0
    clock[0] = 12.0
    manager.record_advertisement(advertisement("switchbot:plug", 0, 0.1, "nine"))
    assert len(publisher.messages) == 2
    clock[0] = 13.0
    manager.record_advertisement(advertisement("switchbot:plug", 0, 0.2, "periodic"))
    assert len(publisher.messages) == 3
    manager.update_registered_sensor("switchbot:plug", {"sensor_id": "plug-003", "display_name": "プラグ", "location": "", "enabled": True})
    clock[0] = 23.0
    manager.record_advertisement(advertisement("switchbot:plug", 0, 0.3, "renamed"))
    assert publisher.messages[-1][0] == "omk/plug-003/power"
    manager.scanning = True
    manager.record_advertisement(advertisement("switchbot:new-plug", 1, 1.0, "candidate"))
    assert manager.candidate_list()[0]["model"] == "plug_sensor"
    assert manager.suggested_sensor_id("switchbot:new-plug") == "plug-001"


def test_waterproof_sensor_setup_uses_th_ids_and_runtime_uses_environment_topic(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:meter", "th-001", "environment", "switchbot", "temperature_humidity_sensor", "", "室内"))
    publisher = _MqttPublisher()
    clock = [0.0]
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])
    outdoor = DecodedAdvertisement(
        "switchbot:waterproof", "switchbot", "waterproof_sensor", "environment", -53, "now",
        {"temperature_c": 27.6, "relative_humidity_percent": 82}, {"manufacturer_data": {"0969": "raw"}},
    )
    manager.scanning = True
    manager.record_advertisement(outdoor)
    assert manager.candidate_list()[0]["model"] == "waterproof_sensor"
    assert manager.suggested_sensor_id("switchbot:waterproof") == "th-002"
    manager.register({"device_key": "switchbot:waterproof", "sensor_id": "th-002", "display_name": "屋外温湿度", "location": "屋外"})
    manager.record_advertisement(outdoor)
    assert manager.registered_list()[1]["latest"]["values"] == {"temperature_c": 27.6, "relative_humidity_percent": 82}
    assert publisher.messages[-1][0] == "omk/th-002/environment"
    assert json.loads(publisher.messages[-1][1]) == {
        "device_id": "th-002", "measured_at": "now", "quality": "normal",
        "source": "direct", "temperature_c": 27.6, "relative_humidity_percent": 82,
    }
    manager.update_registered_sensor("switchbot:waterproof", {"sensor_id": "th-003", "display_name": "浴室温湿度", "location": "浴室", "enabled": True})
    clock[0] = 10.0
    manager.record_advertisement(outdoor)
    assert publisher.messages[-1][0] == "omk/th-003/environment"


def test_disabled_waterproof_sensor_updates_latest_without_publishing(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:waterproof", "th-002", "environment", "switchbot", "waterproof_sensor", "屋外", "屋外温湿度", enabled=False))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.record_advertisement(DecodedAdvertisement(
        "switchbot:waterproof", "switchbot", "waterproof_sensor", "environment", -53, "now",
        {"temperature_c": 27.6, "relative_humidity_percent": 82}, {},
    ))
    assert publisher.messages == []
    assert manager.registered_list()[0]["latest"]["values"] == {"temperature_c": 27.6, "relative_humidity_percent": 82}


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
    assert meter is not None and meter.model == "temperature_humidity_sensor"
    assert co2 is not None and co2.model == "co2_sensor"
    assert malformed is not None and malformed.model == "unknown_switchbot"
    assert unrelated is not None and unrelated.model == "unknown_switchbot"


def test_presence_sensor_pro_decodes_motion_battery_and_light_level() -> None:
    service = {METER_SERVICE_UUID: bytes.fromhex("0020640110ccc8")}
    false = decode(
        "B0:E9:FE:E8:7F:C8", -40,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c0004008c")}, service, "now",
    )
    true = decode(
        "B0:E9:FE:E8:7F:C8", -40,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc81bcc0008008c")}, service, "now",
    )
    changed_light = decode(
        "B0:E9:FE:E8:7F:C8", -40,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c00040087")}, service, "now",
    )
    assert false is not None and false.model == "presence_sensor" and false.sensor_type == "motion"
    assert false.values == {"motion_state": 0, "battery_percent": 100, "light_level": 12}
    assert true is not None and true.values["motion_state"] == 1
    assert changed_light is not None and changed_light.values["light_level"] == 7


def test_presence_sensor_pro_classifier_uses_only_combined_packet_structure() -> None:
    service = {METER_SERVICE_UUID: bytes.fromhex("0020640110ccc8")}
    # Sequence number, motion status, and light level each vary without
    # affecting classification.
    for packet in (
        "b0e9fee87fc8008800040080",
        "b0e9fee87fc8ffcc0004008f",
    ):
        decoded = decode("B0:E9:FE:E8:7F:C8", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, service, "now")
        assert decoded is not None and decoded.model == "presence_sensor"

    malformed_packets = (
        ({SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c000400")}, service),
        ({SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c0004008c")}, {METER_SERVICE_UUID: bytes.fromhex("0020640110cc")}),
        ({SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c0004008c")}, {}),
    )
    for manufacturer_data, service_data in malformed_packets:
        decoded = decode("B0:E9:FE:E8:7F:C8", -40, manufacturer_data, service_data, "now")
        assert decoded is not None and decoded.model == "unknown_switchbot"


def test_presence_sensor_pro_does_not_collide_with_existing_12_byte_layouts() -> None:
    waterproof = decode(
        "D6:69:17:D3:10:38", -53,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("d66917d31038550b069bd200")},
        {METER_SERVICE_UUID: bytes.fromhex("770047")}, "now",
    )
    plug = decode(
        "AC:27:6E:43:26:9E", -50,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("ac276e43269e778010370037")},
        {METER_SERVICE_UUID: bytes.fromhex("6a0064")}, "now",
    )
    assert waterproof is not None and waterproof.model == "waterproof_sensor"
    assert plug is not None and plug.model == "plug_sensor"


def test_presence_sensor_pro_uses_the_existing_motion_setup_id_prefix(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor(
        "switchbot:existing", "motion-001", "motion", "switchbot", "motion_sensor", "", "既存人感",
    ))
    manager = BleManager(registry)
    manager.scanning = True
    decoded = decode(
        "B0:E9:FE:E8:7F:C8", -40,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c0004008c")},
        {METER_SERVICE_UUID: bytes.fromhex("0020640110ccc8")}, "now",
    )
    assert decoded is not None
    manager.record_advertisement(decoded)
    candidate = manager.candidate_list()[0]
    assert candidate["model"] == "presence_sensor"
    assert candidate["sensor_type"] == "motion"
    assert candidate["values"] == {"motion_state": 0, "battery_percent": 100, "light_level": 12}
    assert manager.suggested_sensor_id(decoded.device_key) == "motion-002"
    registered = manager.register({"device_key": decoded.device_key, "sensor_id": "motion-002", "display_name": "Presence"})
    assert (registered.model, registered.sensor_type) == ("presence_sensor", "motion")


def test_presence_sensor_pro_publishes_its_decoded_values_on_the_motion_topic(tmp_path: Path) -> None:
    advertisement = decode(
        "B0:E9:FE:E8:7F:C8", -40,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fee87fc8208c0004008c")},
        {METER_SERVICE_UUID: bytes.fromhex("0020640110ccc8")}, "now",
    )
    assert advertisement is not None
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor(
        advertisement.device_key, "motion-001", "motion", "switchbot", "presence_sensor", "", "Presence",
    ))
    publisher = _MqttPublisher()
    BleManager(registry, publisher).record_advertisement(advertisement)
    assert publisher.messages == [("omk/motion-001/motion", json.dumps({
        "device_id": "motion-001", "measured_at": "now", "motion_state": 0,
        "battery_percent": 100, "light_level": 12, "source": "direct",
    }))]


def _motion_advertisement(state: int, received_at: str = "2026-08-12T14:47:12+09:00") -> DecodedAdvertisement:
    return DecodedAdvertisement("switchbot:motion", "switchbot", "motion_sensor", "motion", -31, received_at, {"motion_state": state}, {"manufacturer_data": {"0969": "raw"}})


def test_motion_publishes_initial_and_state_transitions(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "hall", "廊下"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    for state in (0, 0, 0, 1, 1, 1, 0, 0, 1):
        manager.record_advertisement(_motion_advertisement(state))
    assert [topic for topic, _payload in publisher.messages] == ["omk/motion-001/motion"] * 4
    assert [json.loads(payload)["motion_state"] for _topic, payload in publisher.messages] == [0, 1, 0, 1]
    assert all(set(json.loads(payload)) == {"device_id", "measured_at", "motion_state", "source"} for _topic, payload in publisher.messages)


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


def test_motion_and_contact_publish_current_state_every_ten_seconds_or_on_change(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "", "人感"))
    registry.register(RegisteredSensor("switchbot:motion-2", "motion-002", "motion", "switchbot", "motion_sensor", "", "人感2"))
    registry.register(RegisteredSensor("switchbot:contact", "contact-001", "contact", "switchbot", "contact_sensor", "", "ドア"))
    clock = [0.0]
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher, monotonic_provider=lambda: clock[0])
    manager.record_advertisement(_motion_advertisement(0, "first"))
    manager.record_advertisement(_contact_advertisement(0, "first"))
    clock[0] = 2.0
    manager.record_advertisement(_motion_advertisement(0, "two"))
    manager.record_advertisement(_contact_advertisement(0, "two"))
    assert len(publisher.messages) == 2
    manager.record_advertisement(DecodedAdvertisement("switchbot:motion-2", "switchbot", "motion_sensor", "motion", -40, "independent", {"motion_state": 0}, {}))
    assert len(publisher.messages) == 3
    clock[0] = 3.0
    manager.record_advertisement(_motion_advertisement(1, "changed"))
    manager.record_advertisement(_contact_advertisement(1, "changed"))
    assert [json.loads(payload) for _topic, payload in publisher.messages[-2:]] == [
            {"device_id": "motion-001", "measured_at": "changed", "motion_state": 1, "source": "direct"},
            {"device_id": "contact-001", "measured_at": "changed", "contact_state": 1, "source": "direct"},
    ]
    clock[0] = 13.0
    manager.record_advertisement(_motion_advertisement(1, "periodic"))
    manager.record_advertisement(_contact_advertisement(1, "periodic"))
    assert len(publisher.messages) == 7
    assert manager.registered_list()[0]["latest"]["received_at"] == "periodic"


def test_motion_sensor_id_change_uses_new_topic_and_setup_suggestion(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:motion", "motion-001", "motion", "switchbot", "motion_sensor", "", "人感"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    manager.record_advertisement(_motion_advertisement(0))
    manager.update_registered_sensor("switchbot:motion", {"sensor_id": "motion-002", "display_name": "人感", "location": "", "enabled": True})
    manager.record_advertisement(_motion_advertisement(1))
    assert publisher.messages[-1][0] == "omk/motion-002/motion"
    manager.scanning = True
    manager.record_advertisement(DecodedAdvertisement("switchbot:new-motion", "switchbot", "motion_sensor", "motion", -50, "now", {"motion_state": 0}, {}))
    assert manager.suggested_sensor_id("switchbot:new-motion") == "motion-001"


def test_contact_sensor_decodes_official_service_data_and_manufacturer_layouts() -> None:
    closed = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes.fromhex("64006401068f053d81")}, "now")
    opened = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x02])}, "now")
    timeout = decode("D3:B2:04:E2:31:25", -31, {}, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x04])}, "now")
    manufacturer_closed = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125704c00530051c1")}, {}, "now")
    manufacturer_open = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125715c0059000041")}, {}, "now")
    assert closed is not None and closed.model == "contact_sensor" and closed.sensor_type == "contact" and closed.values == {"contact_state": 0}
    assert opened is not None and opened.values == {"contact_state": 1}
    assert timeout is not None and timeout.values == {"contact_state": 1}
    assert manufacturer_closed is not None and manufacturer_closed.values == {"contact_state": 0}
    assert manufacturer_open is not None and manufacturer_open.values == {"contact_state": 1}

    for packet, expected_state in (
        ("d3b204e231251d4c003a002d80", 0),
        ("d3b204e231251e5c003e0000c0", 1),
        ("d3b204e231251fdc00010003c0", 1),
        ("d3b204e2312520cc00040000c0", 0),
        ("d3b204e2312521dc0007000040", 1),
        ("d3b204e2312522cc000e000140", 0),
        ("d3b204e23125f26c00cd009480", 1),  # timeout-not-close; still open
        # Bytes 8 and 10 are elapsed-time/counter fields, not reserved zeroes.
        ("d3b204e23125066c02a80260c0", 1),
    ):
        decoded = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now")
        assert decoded is not None and decoded.model == "contact_sensor"
        assert decoded.values == {"contact_state": expected_state}

    for packet in (
        "d3b204e23125066d02a80260c0",  # status low nibble is not 0x0c
        "d3b204e23125067c02a80260c0",  # HAL state 3 is reserved
    ):
        decoded = decode("D3:B2:04:E2:31:25", -31, {SWITCHBOT_COMPANY_ID: bytes.fromhex(packet)}, {}, "now")
        assert decoded is not None and decoded.model == "unknown_switchbot"


def test_contact_timeout_not_close_manufacturer_packet_matches_service_hal_state() -> None:
    decoded = decode(
        "D3:B2:04:E2:31:25", -31,
        {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125f26c00cd009480")},
        {METER_SERVICE_UUID: bytes.fromhex("644064030004000780")}, "now",
    )
    assert decoded is not None
    assert decoded.model == "contact_sensor"
    assert decoded.values == {"contact_state": 1}


def test_contact_prefers_current_manufacturer_state_over_stale_service_data() -> None:
    stale_open_service_data = {METER_SERVICE_UUID: bytes.fromhex("64006405011300d981")}
    manufacturer_closed = {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125854c00450035c1")}
    manufacturer_open = {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125865c004a000041")}
    closed = decode("D3:B2:04:E2:31:25", -31, manufacturer_closed, stale_open_service_data, "now")
    opened = decode("D3:B2:04:E2:31:25", -31, manufacturer_open, {METER_SERVICE_UUID: bytes([0x64, 0, 0x64, 0x01])}, "now")
    assert closed is not None and closed.model == "contact_sensor" and closed.values == {"contact_state": 0}
    assert opened is not None and opened.model == "contact_sensor" and opened.values == {"contact_state": 1}


def test_contact_decoder_does_not_misclassify_existing_sensor_layouts() -> None:
    meter = decode("CF:39:41:C7:ED:79", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cf3941c7ed79f40304992c")}, {}, "now")
    co2 = decode("B0:E9:FE:58:15:CC", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("b0e9fe5815ccf6e405982e0024020e00")}, {}, "now")
    motion = decode("CF:FC:6A:48:DB:15", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("cffc6a48db150c6c0000")}, {}, "now")
    malformed = decode("D3:B2:04:E2:31:25", -40, {}, {METER_SERVICE_UUID: bytes.fromhex("64")}, "now")
    unrelated = decode("D3:B2:04:E2:31:25", -40, {SWITCHBOT_COMPANY_ID: bytes.fromhex("d3b204e23125707c01530051c1")}, {}, "now")
    assert meter is not None and meter.model == "temperature_humidity_sensor"
    assert co2 is not None and co2.model == "co2_sensor"
    assert motion is not None and motion.model == "motion_sensor"
    assert malformed is not None and malformed.model == "unknown_switchbot"
    assert unrelated is not None and unrelated.model == "unknown_switchbot"


def _contact_advertisement(state: int, received_at: str = "2026-08-12T14:47:12+09:00") -> DecodedAdvertisement:
    return DecodedAdvertisement("switchbot:contact", "switchbot", "contact_sensor", "contact", -31, received_at, {"contact_state": state}, {"manufacturer_data": {"0969": "raw"}})


def test_contact_publishes_initial_and_open_closed_transitions(tmp_path: Path) -> None:
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:contact", "contact-001", "contact", "switchbot", "contact_sensor", "door", "ドア"))
    publisher = _MqttPublisher()
    manager = BleManager(registry, publisher)
    for state in (0, 0, 1, 1, 0, 0, 1):
        manager.record_advertisement(_contact_advertisement(state))
    assert [topic for topic, _payload in publisher.messages] == ["omk/contact-001/contact"] * 4
    assert [json.loads(payload)["contact_state"] for _topic, payload in publisher.messages] == [0, 1, 0, 1]


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
