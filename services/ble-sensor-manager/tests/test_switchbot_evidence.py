"""Synthetic identities; layout variants are separate from address substitution."""
import json
from types import SimpleNamespace

import pytest

from omk_ble import switchbot as sb
from omk_ble.models import RegisteredSensor
from omk_ble.registry import SensorRegistry
from omk_ble.service import BleManager

ADDRESS = "02:00:00:00:00:40"
IDENTITY = "020000000040"
NODE = "020000000041"
NOW = "2026-09-10T00:00:00+00:00"
# Tail bytes retain observed layout/variation; first six bytes are synthetic.
CASES = [
    ("meter", "temperature_humidity_sensor", "3903069838", "540064069838", {"temperature_c": 24.6, "relative_humidity_percent": 56, "battery_percent": 100}),
    ("meter_plus", "temperature_humidity_sensor", "3903069838", "690064069838", {"temperature_c": 24.6, "relative_humidity_percent": 56, "battery_percent": 100}),
    ("co2", "co2_sensor", "0ae4029c250334022600", "350064", {"temperature_c": 28.2, "relative_humidity_percent": 37, "co2_ppm": 550}),
    ("motion", "motion_sensor", "376c0001", "734064000002", {"motion_state": 1}),
    ("presence", "presence_sensor", "208c0004008c", "0020640110ccc8", {"motion_state": 0, "battery_percent": 100, "light_level": 12}),
    ("contact", "contact_sensor", "704c00530051c1", "64006401068f053d81", {"contact_state": 0}),
    ("plug", "plug_sensor", "358010360029", "6a0064", {"switch_state": 1, "power_w": 4.1}),
    ("outdoor", "waterproof_sensor", "d20b019dca00", "770064", {"temperature_c": 29.1, "relative_humidity_percent": 74}),
]


def fields(tail, service):
    return ({sb.SWITCHBOT_COMPANY_ID: bytes.fromhex(IDENTITY + tail)} if tail is not None else {},
            {sb.METER_SERVICE_UUID: bytes.fromhex(service)} if service is not None else {})


def packet(tail, service=None, hint=None):
    md, sd = fields(tail, service)
    return sb.decode(ADDRESS, -50, md, sd, NOW, model_hint=hint)


def unknown(result):
    assert result is not None
    assert (result.model, result.sensor_type, result.values) == ("unknown_switchbot", "unknown", {})


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
def test_normal_supported_frames(kind, model, tail, service, values):
    decoded = packet(tail, service, model if kind == "presence" else None)
    assert decoded.model == model
    assert decoded.values == values


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
def test_manufacturer_layout_never_discovers_model(kind, model, tail, service, values):
    unknown(packet(tail))
    # A hint is separately supplied by physical registration, not inferred here.
    hinted = packet(tail, hint=model)
    if kind == "presence":
        assert hinted.model == model
        assert hinted.values == {"motion_state": 0, "light_level": 12}
    else:
        assert hinted.model == model
        assert hinted.values


@pytest.mark.parametrize("service,expected", [("6a0064", "plug_sensor"), ("670064", "plug_sensor"), ("770064", "waterproof_sensor")])
def test_two_valid_layouts_are_resolved_by_service_not_code_order(service, expected, monkeypatch):
    tail = "7f8010960000"  # Both Plug=ON/0 W and Outdoor=22 C/0% are plausible.
    company = bytes.fromhex(IDENTITY + tail)
    assert sb._decode_plug_manufacturer_data(company) is not None
    assert sb._decode_waterproof_manufacturer_data(company) is not None
    assert packet(tail, service).model == expected
    monkeypatch.setattr(sb, "MANUFACTURER_DECODERS", dict(reversed(list(sb.MANUFACTURER_DECODERS.items()))))
    assert packet(tail, service).model == expected
    unknown(packet(tail))
    assert sb.unconfirmed_model_options(packet(tail)) == {}


@pytest.mark.parametrize("hint,service", [("plug_sensor", "770064"), ("waterproof_sensor", "6a0064")])
def test_explicit_model_conflict_cannot_be_overridden_by_hint(hint, service):
    decoded = packet("7f8010960000", service, hint)
    unknown(decoded)
    assert decoded.raw["classification"]["evidence"] == "conflicting_model_hint"


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
def test_unknown_service_cannot_fall_back_to_manufacturer_or_hint(kind, model, tail, service, values):
    for hint in (None, model):
        unknown(packet(tail, "010064", hint))
        unknown(packet(tail, "", hint))


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
def test_service_battery_over_100_rejects_whole_packet(kind, model, tail, service, values):
    payload = bytearray.fromhex(service)
    payload[2] = 101
    decoded = packet(tail, payload.hex(), model)
    unknown(decoded)
    assert decoded.raw["classification"]["status"] == "invalid_payload"


@pytest.mark.parametrize("device_type", [0x54, 0x69])
@pytest.mark.parametrize("index,value", [(2, 127), (2, 229), (3, 10), (4, 0xFF), (4, 21), (5, 101), (5, 255)])
def test_meter_service_value_validation_without_manufacturer(device_type, index, value):
    service = bytearray([device_type, 0, 90, 3, 0x99, 52])
    service[index] = value
    unknown(packet(None, service.hex()))


@pytest.mark.parametrize("tail", ["39030a9838", "390306ff38", "3903069865"])
def test_meter_manufacturer_invalid_values_are_not_rescued_by_service(tail):
    unknown(packet(tail, hint="temperature_humidity_sensor"))
    unknown(packet(tail, "540064069838"))


def test_meter_alarm_scale_bits_are_not_decimal_or_humidity():
    result = packet(None, "d400e4c999e4")
    assert result.model == "temperature_humidity_sensor"
    assert result.values == {"temperature_c": 25.9, "relative_humidity_percent": 100, "battery_percent": 100}


@pytest.mark.parametrize("ppm,valid", [(399, False), (400, True), (10000, True), (10001, False), (65535, False)])
@pytest.mark.parametrize("last_byte", ["00", "40"])
def test_co2_range_is_value_validation(ppm, valid, last_byte):
    tail = "0ae4029c250334" + ppm.to_bytes(2, "big").hex() + last_byte
    result = packet(tail, "350064")
    if valid:
        assert result.values["co2_ppm"] == ppm
    else:
        unknown(result)
    unknown(packet(tail))  # A normal ppm value does not establish a model.


@pytest.mark.parametrize("tail", ["0ae40a9c250334022600", "0ae402ff250334022600", "0ae4029c650334022600", "0ae4029c2503340226", "0ae4029c25033402260000"])
def test_co2_invalid_manufacturer_is_rejected_with_known_type_or_hint(tail):
    unknown(packet(tail, "350064"))
    unknown(packet(tail, hint="co2_sensor"))


def test_co2_layout_with_other_sdk_type_is_not_misidentified_as_outdoor():
    unknown(packet("0ae4029c250334022600", "770064"))


@pytest.mark.parametrize("service", ["0020640110ccc8", "ffffffffffffff", "00000000000000", "1020640110ccc8"])
def test_presence_same_length_service_is_not_unique_model_evidence(service):
    unknown(packet("208c0004008c", service))


@pytest.mark.parametrize("tail,service", [
    ("208c0004008c", "0020650110ccc8"),  # battery 101
    ("20ff0004008c", "0020640110ccc8"),  # undefined status bits
    ("208c0004008c", "0020640110cc"),    # truncated service
    ("208c000400", "0020640110ccc8"),    # truncated manufacturer
    ("208c0004008c", "ffffffffffffff"),
    ("208c0004008c", "0120640110ccc8"),
])
def test_presence_hint_does_not_accept_invalid_payload(tail, service):
    unknown(packet(tail, service, "presence_sensor"))


@pytest.mark.parametrize("tail", ["008800040080", "ffcc0004008f", "1bcc0008008c"])
def test_presence_supported_status_sequence_light_variants_need_hint(tail):
    result = packet(tail, "0020640110ccc8", "presence_sensor")
    assert result.model == "presence_sensor"
    unknown(packet(tail, "0020640110ccc8"))


@pytest.mark.parametrize("status", [0x00, 0x40, 0x80, 0xC0])
def test_motion_official_service_needs_no_manufacturer(status):
    service = bytes([0x73, status, 100, 0xAB, 0xCD, 0x22]).hex()
    result = packet(None, service)
    assert result.model == "motion_sensor"
    assert result.values["motion_state"] == int(bool(status & 0x40))


@pytest.mark.parametrize("status", [0x2C, 0x6C, 0xAC, 0xEC, 0x2D, 0xFF])
def test_motion_manufacturer_status_boundaries(status):
    result = packet(bytes([0x17, status, 0xDA, 0x59]).hex(), hint="motion_sensor")
    if status & 0x3F == 0x2C:
        assert result.model == "motion_sensor"
    else:
        unknown(result)


@pytest.mark.parametrize("service", ["734064000000", "734064000003", "73406400000e", "734064000042", "732c64000002", "7340640000"])
def test_motion_invalid_status_encoding(service):
    unknown(packet(None, service))


@pytest.mark.parametrize("service", ["64006406", "640064060000000000", "e40064010000000000", "64007f010000000000", "6400640100000000"])
def test_contact_invalid_service_cannot_fall_back_to_manufacturer(service):
    unknown(packet("704c00530051c1", service, "contact_sensor"))


@pytest.mark.parametrize("watts", [0, 0.1, 4.1, 5.5, 173.2])
@pytest.mark.parametrize("state", [0, 0x80])
@pytest.mark.parametrize("flags", [0, 0x10, 0x16])
def test_plug_zero_low_power_state_and_variable_flags(watts, state, flags):
    raw = round(watts * 10)
    tail = bytes([0, state, flags, 0, raw >> 8, raw & 255]).hex()
    result = packet(tail, "6a0064")
    assert result.model == "plug_sensor"
    assert result.values == {"switch_state": int(bool(state)), "power_w": watts}
    assert packet(tail, hint="plug_sensor").values == result.values
    unknown(packet(tail))


def test_plug_overload_flag_is_not_power_or_model():
    assert packet("7080163886c4", "6a0064").values["power_w"] == 173.2


def test_outdoor_negative_temperature_and_missing_service():
    result = packet("550b06055200", "770064")
    assert result.values == {"temperature_c": -5.6, "relative_humidity_percent": 82}
    assert packet("550b06055200", hint="waterproof_sensor").values == result.values
    unknown(packet("550b06055200"))


@pytest.mark.parametrize("tail,model", [
    ("3903069838", "temperature_humidity_sensor"), ("3a03069835", "temperature_humidity_sensor"),
    ("3b03089835", "temperature_humidity_sensor"), ("3c03009935", "temperature_humidity_sensor"),
    ("3d03009933", "temperature_humidity_sensor"),
    ("0ae4029c250334022600", "co2_sensor"), ("788006992a002d027000", "co2_sensor"),
    ("376c0001", "motion_sensor"), ("172cda59", "motion_sensor"),
    ("066c02a80260c0", "contact_sensor"), ("f26c00cd009480", "contact_sensor"),
    ("d20b019dca00", "waterproof_sensor"), ("d30b029dca00", "waterproof_sensor"),
    ("358010360029", "plug_sensor"), ("368010350029", "plug_sensor"),
])
def test_observed_variable_bytes_are_not_fixed_model_discriminators(tail, model):
    assert packet(tail, hint=model).model == model
    unknown(packet(tail))


@pytest.mark.parametrize("address,physical", [("02:00:00:00:00:50", "020000000051"), ("02:00:00:00:00:52", "020000000053")])
def test_address_substitution_only_checks_identity_independence(address, physical):
    # Distinct from the byte-variation test above: this proves identity handling
    # only, not that the decoder generalizes to another firmware/device layout.
    result = sb.decode(address, -50, {sb.SWITCHBOT_COMPANY_ID: bytes.fromhex(physical + "358010360029")}, {sb.METER_SERVICE_UUID: bytes.fromhex("6a0064")}, NOW)
    assert result.device_key == "switchbot:" + physical
    assert result.model == "plug_sensor"


class Publisher:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, **kwargs):
        self.messages.append((topic, json.loads(payload)))


def relay_json(md, sd):
    return {
        "protocol_version": 1, "relay_node_id": NODE, "ble_address": IDENTITY, "rssi": -50,
        "manufacturer_data": [{"company_id": company, "data": data.hex()} for company, data in md.items()],
        "service_data": [{"uuid": uuid, "data": data.hex()} for uuid, data in sd.items()],
    }


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
@pytest.mark.parametrize("registered", [True, False])
def test_direct_and_relay_share_model_values_and_physical_identity(tmp_path, monkeypatch, kind, model, tail, service, values, registered):
    md, sd = fields(tail, service)
    registry = SensorRegistry(tmp_path / "sensors.json")
    expected = packet(tail, service, model if registered else None)
    if registered:
        registry.register(RegisteredSensor("switchbot:" + IDENTITY, "test-sensor", expected.sensor_type, "switchbot", model, "", "Test"))
    monkeypatch.setattr("omk_ble.service.now_iso", lambda: NOW)
    direct_publisher, relay_publisher = Publisher(), Publisher()
    direct, relay = BleManager(registry, direct_publisher), BleManager(registry, relay_publisher)
    direct.scanning = True
    direct._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    raw = relay_json(md, sd)
    parsed = relay._parse_relay_observation(raw, NODE)
    relay_decoded = sb.decode(*parsed, NOW, model_hint=relay._registered_model_hint(parsed[0], parsed[2]))
    observed = direct.observations[expected.device_key]
    assert (observed.model, observed.values, observed.device_key) == (relay_decoded.model, relay_decoded.values, relay_decoded.device_key)
    relay.handle_relay_mqtt(f"omk-relay/{NODE}/ble/raw", json.dumps(raw).encode())
    if registered:
        assert observed.model == model and observed.values == values
        assert direct_publisher.messages[0][1]["source"] == "direct"
        assert relay_publisher.messages[0][1]["source"] == "relay"
        assert direct.registered_list()[0]["latest"]["values"] == values
        for key, value in values.items():
            assert direct_publisher.messages[0][1][key] == relay_publisher.messages[0][1][key] == value
    else:
        assert direct_publisher.messages == relay_publisher.messages == []
        candidate = direct.candidate_list()[0]
        assert candidate["model"] == expected.model
        if kind == "presence":
            with pytest.raises(ValueError):
                direct.register({"device_key": expected.device_key, "sensor_id": "test-sensor", "display_name": "Test"})


@pytest.mark.parametrize("kind,model,tail,service,values", CASES)
def test_fragment_missing_service_never_changes_to_another_model(kind, model, tail, service, values):
    unknown(packet(tail))  # manufacturer fragment before SCAN_RSP
    complete = packet(tail, service)
    assert complete.model in (model, "unknown_switchbot")
    hinted_fragment = packet(tail, hint=model)
    assert hinted_fragment.model in (model, "unknown_switchbot")


def test_conflicting_service_fields_fail_closed_independent_of_order():
    md, _ = fields("7f8010960000", None)
    entries = [(sb.METER_SERVICE_UUID, bytes.fromhex("6a0064")), (sb.METER_SERVICE_UUID.upper(), bytes.fromhex("770064"))]
    for parts in (entries, list(reversed(entries))):
        unknown(sb.decode(ADDRESS, -50, md, dict(parts), NOW))
        assert BleManager._parse_relay_observation(relay_json(md, dict(parts)), NODE) is None


def test_relay_length_limit_rejects_instead_of_truncating():
    md, sd = fields("358010360029", "6a0064")
    raw = relay_json(md, sd)
    raw["manufacturer_data"][0]["data"] = "00" * 32
    assert BleManager._parse_relay_observation(raw, NODE) is None


def test_conflicting_registration_does_not_publish_or_display_values_as_old_model(tmp_path, monkeypatch):
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:" + IDENTITY, "test-sensor", "environment", "switchbot", "waterproof_sensor", "", "Test"))
    before = registry.path.read_bytes()
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    monkeypatch.setattr("omk_ble.service.now_iso", lambda: NOW)
    md, sd = fields("7f8010960000", "6a0064")
    manager._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    manager.handle_relay_mqtt(f"omk-relay/{NODE}/ble/raw", json.dumps(relay_json(md, sd)).encode())
    assert publisher.messages == []
    assert manager.registered_list()[0]["latest"]["values"] == {}
    assert manager.registered_list()[0]["status"] == "unrecognized"
    assert registry.path.read_bytes() == before


@pytest.mark.parametrize("tail,service", [
    ("208c0004008c", "0020640110ccc8"),  # historical unoccupied capture
    ("1bcc0008008c", "0020640110ccc8"),  # historical occupied capture
    ("21c800040087", "00203c0110ccc8"),  # synthetic battery/light/status variant
])
def test_legacy_presence_requires_operator_choice_and_survives_restart(tmp_path, monkeypatch, tail, service):
    from omk_ble import main as ble_main

    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    candidate = packet(tail, service)
    unknown(candidate)
    manager.record_advertisement(candidate)
    listed = manager.candidate_list()[0]
    assert listed["model"] == "unknown_switchbot" and listed["values"] == {}
    assert listed["manual_registration_models"] == ["presence_sensor"]
    assert listed["unconfirmed_preview"] == {"model": "presence_sensor", "values": packet(tail, service, "presence_sensor").values}
    assert publisher.messages == [] and registry.list() == []
    monkeypatch.setattr(ble_main, "manager", manager)
    request = dict(device_key=candidate.device_key, sensor_id="test-presence", display_name="Test")
    with pytest.raises(ValueError):
        manager.register(request)
    suggestion = ble_main.suggested_sensor_id(candidate.device_key, "presence_sensor")
    assert suggestion == {"sensor_id": "motion-001"}
    assert registry.list() == []  # preview/ID suggestion never creates a hint
    registered = ble_main.register(ble_main.RegisterRequest(**request, confirmed_model="presence_sensor"))
    assert registered["model"] == "presence_sensor"
    assert manager.observations[candidate.device_key].raw["classification"]["evidence"] == "user_confirmed_presence_sensor"
    assert manager.candidate_list() == []
    restarted = BleManager(SensorRegistry(registry.path), publisher)
    md, sd = fields(tail, service)
    restarted._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    assert restarted.observations[candidate.device_key].model == "presence_sensor"
    assert restarted.registered_list()[0]["latest"]["values"] == packet(tail, service, "presence_sensor").values
    assert publisher.messages[-1][1]["motion_state"] == packet(tail, service, "presence_sensor").values["motion_state"]
    restarted.delete_registered_sensor(candidate.device_key)
    assert restarted._registered_model_hint(ADDRESS, md) is None
    unknown(packet(tail, service, restarted._registered_model_hint(ADDRESS, md)))


@pytest.mark.parametrize("tail,service", [
    ("208c0004008c", "0020650110ccc8"),
    ("20ff0004008c", "0020640110ccc8"),
    ("208c0004008c", "0020640110cc"),
    ("208c000400", "0020640110ccc8"),
    ("208c0004008c", "0120640110ccc8"),
    ("208c0004008c", "ffffffffffffff"),
    ("208c0004008c", "00000000000000"),
    ("7f8010960000", "6a0064"),
    ("7f8010960000", "770064"),
    ("376c0001", "734064000002"),
    ("704c00530051c1", "64006401068f053d81"),
    # Correct length and prefix cannot override incompatible status/light.
    ("358010360029", "0020640110ccc8"),
    ("d20b019dca00", "0020640110ccc8"),
])
def test_manual_choice_cannot_override_unsupported_or_contradictory_frames(tmp_path, tail, service):
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    candidate = packet(tail, service)
    manager.record_advertisement(candidate)
    assert "manual_registration_models" not in manager.candidate_list()[0]
    with pytest.raises(ValueError):
        manager.register(dict(device_key=candidate.device_key, sensor_id="test-presence", display_name="Test", confirmed_model="presence_sensor"))
    assert manager.registry.list() == []


def test_manual_choice_revalidates_latest_candidate_and_rejects_other_models(tmp_path):
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    candidate = packet("208c0004008c", "0020640110ccc8")
    manager.record_advertisement(candidate)
    request = dict(device_key=candidate.device_key, sensor_id="test-presence", display_name="Test")
    with pytest.raises(ValueError):
        manager.register(dict(request, confirmed_model="plug_sensor"))
    assert manager.candidate_list()[0]["manual_registration_models"]
    manager.record_advertisement(packet("208c0004008c", "0020650110ccc8"))
    with pytest.raises(ValueError):
        manager.register(dict(request, confirmed_model="presence_sensor"))
    assert manager.registry.list() == []


def test_presence_preview_updates_without_hint_or_telemetry(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    first = packet("208c0004008c", "0020640110ccc8")
    manager.record_advertisement(first)
    first_item = manager.candidate_list()[0]
    assert first_item["model"] == "unknown_switchbot"
    assert first_item["values"] == {}
    assert first_item["unconfirmed_preview"] == {"model": "presence_sensor", "values": {
        "motion_state": 0, "battery_percent": 100, "light_level": 12,
    }}
    assert registry.list() == [] and publisher.messages == []

    # A later valid packet replaces preview-only values without making a model
    # decision or publishing an observation as a normal sensor measurement.
    updated = packet("1bcc00080087", "00203c0110ccc8")
    manager.record_advertisement(updated)
    item = manager.candidate_list()[0]
    assert item["unconfirmed_preview"] == {"model": "presence_sensor", "values": {
        "motion_state": 1, "battery_percent": 60, "light_level": 7,
    }}
    assert item["received_at"] == NOW
    assert registry.list() == [] and publisher.messages == []
    assert manager.observations[item["device_key"]].model == "unknown_switchbot"


def test_presence_manufacturer_only_requires_confirmation_then_publishes_without_battery(tmp_path, monkeypatch):
    """Anonymous Pi 4 capture: fd3d service data was consistently absent."""
    from omk_ble import main as ble_main

    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    inactive = packet("0a8c00350091")
    unknown(inactive)
    manager.record_advertisement(inactive)
    item = manager.candidate_list()[0]
    assert item["manual_registration_models"] == ["presence_sensor"]
    assert item["unconfirmed_preview"] == {"model": "presence_sensor", "values": {
        "motion_state": 0, "light_level": 1,
    }}
    monkeypatch.setattr(ble_main, "manager", manager)
    registered = ble_main.register(ble_main.RegisterRequest(
        device_key=inactive.device_key, sensor_id="test-presence", display_name="Test",
        confirmed_model="presence_sensor",
    ))
    assert registered["model"] == "presence_sensor"

    active = packet("0fcc00010091", hint="presence_sensor")
    assert active.model == "presence_sensor"
    assert active.raw["classification"]["evidence"] == "registered_hint"
    assert active.values == {"motion_state": 1, "light_level": 1}
    manager.record_advertisement(active)
    assert publisher.messages[-1][1]["motion_state"] == 1
    assert "battery_percent" not in publisher.messages[-1][1]


@pytest.mark.parametrize("tail", [
    "358010360029",  # Plug Mini fixture
    "d20b019dca00",  # Waterproof Sensor fixture
])
def test_existing_12_byte_plug_and_waterproof_forms_are_not_presence_choices(tail):
    candidate = packet(tail)
    unknown(candidate)
    assert "presence_sensor" not in sb.unconfirmed_model_options(candidate)


@pytest.mark.parametrize("tail,service", [
    ("208c0004008c", "0020650110ccc8"),  # invalid battery
    ("20ff0004008c", "0020640110ccc8"),  # invalid status bits
    ("358010360029", "0020640110ccc8"),  # Plug-shaped manufacturer payload
    ("d20b019dca00", "0020640110ccc8"),  # Outdoor-shaped manufacturer payload
    ("376c0001", "734064000002"),         # explicit Motion type
    ("704c00530051c1", "64006401068f053d81"),  # explicit Contact type
])
def test_invalid_or_other_unknown_switchbot_has_no_presence_preview(tmp_path, tail, service):
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    manager.record_advertisement(packet(tail, service))
    item = manager.candidate_list()[0]
    assert "unconfirmed_preview" not in item
    assert "manual_registration_models" not in item


def test_registered_presence_uses_normal_values_not_preview(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:" + IDENTITY, "test-presence", "motion", "switchbot", "presence_sensor", "", "Test"))
    manager = BleManager(registry)
    md, sd = fields("1bcc00080087", "00203c0110ccc8")
    manager._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    assert manager.candidate_list() == []
    assert manager.registered_list()[0]["latest"]["values"] == {
        "motion_state": 1, "battery_percent": 60, "light_level": 7,
    }


def test_motion_manufacturer_only_requires_confirmation_before_registration(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    inactive = packet("372c0001")  # 10-byte observed Motion layout, no service field
    unknown(inactive)
    manager.record_advertisement(inactive)
    listed = manager.candidate_list()[0]
    assert listed["model"] == "unknown_switchbot" and listed["values"] == {}
    assert listed["manual_registration_models"] == ["motion_sensor"]
    assert listed["unconfirmed_preview"] == {"model": "motion_sensor", "values": {"motion_state": 0}}
    assert publisher.messages == [] and registry.list() == []


@pytest.mark.parametrize("service,tail,expected", [
    ("73806400dd0a", "372cda59", 0),  # service=0, manufacturer=0
    ("73806400dd0a", "376cda59", 1),  # service=0, fresher manufacturer=1
    ("73406400dd0a", "372cda59", 0),  # service=1, fresher manufacturer=0
])
def test_motion_service_identifies_model_and_valid_manufacturer_status_wins(service, tail, expected):
    result = packet(tail, service)
    assert result.model == "motion_sensor"
    assert result.values == {"motion_state": expected}


def test_motion_malformed_manufacturer_falls_back_to_valid_service_and_other_service_does_not():
    assert packet("37000001", "73806400dd0a").values == {"motion_state": 0}
    other = packet("376cda59", "6a0064")
    assert other.model == "unknown_switchbot"
    assert other.sensor_type == "unknown"


def test_registered_motion_still_decodes_manufacturer_only_and_direct_relay_match(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    registry.register(RegisteredSensor("switchbot:" + IDENTITY, "test-motion", "motion", "switchbot", "motion_sensor", "", "Test"))
    md, sd = fields("376cda59", None)
    direct = packet("376cda59", hint="motion_sensor")
    assert direct.model == "motion_sensor" and direct.values == {"motion_state": 1}
    relay = sb.decode(*BleManager._parse_relay_observation(relay_json(md, sd), NODE), NOW, model_hint="motion_sensor")
    assert (relay.model, relay.values, relay.device_key) == (direct.model, direct.values, direct.device_key)
    manager = BleManager(registry)
    manager._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    assert manager.registered_list()[0]["latest"]["values"] == {"motion_state": 1}


def test_unconfirmed_options_include_unique_motion_manufacturer_only_form():
    assert list(sb.unconfirmed_model_options(packet("208c0004008c", "0020640110ccc8"))) == ["presence_sensor"]
    assert list(sb.unconfirmed_model_options(packet("376c0001"))) == ["motion_sensor"]


@pytest.mark.parametrize(("model", "tail", "sensor_id", "topic", "values"), [
    ("waterproof_sensor", "d20b019dca00", "th-001", "omk/th-001/environment", {"temperature_c": 29.1, "relative_humidity_percent": 74}),
    ("motion_sensor", "376c0001", "motion-001", "omk/motion-001/motion", {"motion_state": 1}),
])
def test_manufacturer_only_waterproof_and_motion_require_confirmation_then_publish(
    tmp_path, model, tail, sensor_id, topic, values,
):
    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    observed = packet(tail)
    unknown(observed)
    manager.record_advertisement(observed)
    item = manager.candidate_list()[0]
    assert item["model"] == "unknown_switchbot" and item["values"] == {}
    assert item["manual_registration_models"] == [model]
    assert item["unconfirmed_preview"] == {"model": model, "values": values}
    with pytest.raises(ValueError):
        manager.register({"device_key": observed.device_key, "sensor_id": sensor_id, "display_name": "Test"})

    registered = manager.register({
        "device_key": observed.device_key, "sensor_id": sensor_id, "display_name": "Test", "confirmed_model": model,
    })
    assert registered.model == model
    restarted = BleManager(SensorRegistry(registry.path), publisher)
    manufacturer, service = fields(tail, None)
    restarted._on_detection(
        SimpleNamespace(address=ADDRESS),
        SimpleNamespace(rssi=-50, manufacturer_data=manufacturer, service_data=service),
    )
    assert restarted.observations[observed.device_key].values == values
    assert publisher.messages[-1][0] == topic
    assert all(publisher.messages[-1][1][key] == value for key, value in values.items())


@pytest.mark.parametrize("tail", [
    "d20b019dca01",  # Waterproof reserved marker is invalid.
    "d20b019dca",    # Waterproof length is invalid.
    "37000001",      # Motion status low bits are invalid.
    "376c00",        # Motion length is invalid.
])
def test_invalid_waterproof_and_motion_manufacturer_packets_have_no_confirmation_option(tail):
    candidate = packet(tail)
    unknown(candidate)
    assert sb.unconfirmed_model_options(candidate) == {}


@pytest.mark.parametrize("service", ["540064009834", "690064009834"])
def test_meter_explicit_service_types_still_identify_the_shared_model(service):
    result = packet("3903009834", service)
    assert result.model == "temperature_humidity_sensor"
    assert result.values == {"temperature_c": 24.0, "relative_humidity_percent": 52, "battery_percent": 100}


def test_meter_manufacturer_only_preview_requires_confirmation_and_persists_hint(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    observed = packet("3903009834")  # observed-compatible 24.0 C / 52 % layout
    unknown(observed)
    manager.record_advertisement(observed)
    item = manager.candidate_list()[0]
    assert item["model"] == "unknown_switchbot" and item["values"] == {}
    assert item["manual_registration_models"] == ["temperature_humidity_sensor"]
    assert item["unconfirmed_preview"] == {
        "model": "temperature_humidity_sensor",
        "values": {"temperature_c": 24.0, "relative_humidity_percent": 52},
    }
    assert registry.list() == [] and publisher.messages == []

    # Vary both measured fields, not the physical identifier.
    changed = packet("3a03019835")
    manager.record_advertisement(changed)
    assert manager.candidate_list()[0]["unconfirmed_preview"]["values"] == {
        "temperature_c": 24.1, "relative_humidity_percent": 53,
    }
    request = dict(device_key=changed.device_key, sensor_id="test-meter", display_name="Test")
    with pytest.raises(ValueError):
        manager.register(request)
    registered = manager.register(dict(request, confirmed_model="temperature_humidity_sensor"))
    assert registered.model == "temperature_humidity_sensor"
    assert registry.list()[0].model == "temperature_humidity_sensor"
    assert manager.candidate_list() == []

    restarted = BleManager(SensorRegistry(registry.path), publisher)
    md, sd = fields("3a03019835", None)
    restarted._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(rssi=-50, manufacturer_data=md, service_data=sd))
    assert restarted.observations[changed.device_key].model == "temperature_humidity_sensor"
    assert restarted.registered_list()[0]["latest"]["values"] == {
        "temperature_c": 24.1, "relative_humidity_percent": 53,
    }
    assert publisher.messages[-1][1]["temperature_c"] == 24.1


@pytest.mark.parametrize("tail,service", [
    ("39030a9834", None),               # invalid fractional nibble
    ("3903009865", None),               # RH > 100
    ("39030098", None),                 # truncated layout
    ("376c0001", None),                 # Motion
    ("358010360029", None),             # Plug
    ("d20b019dca00", None),             # Outdoor
    ("3903009834", "6a0064"),          # explicit Plug service
    ("3903009834", "734064000002"),    # explicit Motion service
])
def test_meter_preview_rejects_invalid_other_and_explicit_non_meter_frames(tmp_path, tail, service):
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    manager.record_advertisement(packet(tail, service))
    item = manager.candidate_list()[0]
    assert "temperature_humidity_sensor" not in item.get("manual_registration_models", [])
    assert item.get("unconfirmed_preview", {}).get("model") != "temperature_humidity_sensor"


def test_unconfirmed_options_are_unambiguous_for_presence_and_meter():
    assert list(sb.unconfirmed_model_options(packet("208c0004008c", "0020640110ccc8"))) == ["presence_sensor"]
    assert list(sb.unconfirmed_model_options(packet("3903009834"))) == ["temperature_humidity_sensor"]


CO2_OBSERVED = "b0e9fe5815cc1ae405992d003b02fe00"


def co2_observed_packet(payload=CO2_OBSERVED, service=None, hint=None):
    return sb.decode(ADDRESS, -50, {sb.SWITCHBOT_COMPANY_ID: bytes.fromhex(payload)},
                     {} if service is None else {sb.METER_SERVICE_UUID: bytes.fromhex(service)}, NOW, hint)


@pytest.mark.parametrize("payload,ppm", [
    ("b0e9fe5815ccffe4019a31002502ec00", 748),
    ("b0e9fe5815cc01e4019a3100250e8040", 3712),
])
@pytest.mark.parametrize("service,hint", [("350064", None), (None, "co2_sensor")])
def test_co2_observed_normal_and_high_concentrations_decode(payload, ppm, service, hint):
    result = co2_observed_packet(payload, service, hint)
    assert result is not None
    assert result.device_key == "switchbot:b0e9fe5815cc"
    assert (result.model, result.sensor_type) == ("co2_sensor", "environment")
    assert result.raw["classification"]["status"] == "decoded"
    assert result.values == {"temperature_c": 26.1, "relative_humidity_percent": 49, "co2_ppm": ppm}


@pytest.mark.parametrize("last_byte", ["00", "01", "40", "ff"])
def test_co2_last_byte_does_not_affect_unconfirmed_model_options(last_byte):
    observed = co2_observed_packet(CO2_OBSERVED[:-2] + last_byte)
    unknown(observed)
    options = sb.unconfirmed_model_options(observed)
    assert list(options) == ["co2_sensor"]
    assert options["co2_sensor"].values["co2_ppm"] == 766


def test_co2_explicit_service_still_auto_identifies_the_observed_layout():
    result = co2_observed_packet(service="350064")
    assert result.model == "co2_sensor"
    assert result.values == {"temperature_c": 25.5, "relative_humidity_percent": 45, "co2_ppm": 766}
    service_only = packet(None, "350064")
    assert (service_only.model, service_only.sensor_type, service_only.values) == ("co2_sensor", "environment", {})


def test_co2_manufacturer_only_preview_requires_confirmation_and_persists_hint(tmp_path):
    registry = SensorRegistry(tmp_path / "sensors.json")
    publisher = Publisher()
    manager = BleManager(registry, publisher)
    manager.scanning = True
    observed = co2_observed_packet()
    unknown(observed)
    manager.record_advertisement(observed)
    item = manager.candidate_list()[0]
    assert item["model"] == "unknown_switchbot" and item["values"] == {}
    assert item["manual_registration_models"] == ["co2_sensor"]
    assert item["unconfirmed_preview"] == {
        "model": "co2_sensor",
        "values": {"temperature_c": 25.5, "relative_humidity_percent": 45, "co2_ppm": 766},
    }
    assert registry.list() == [] and publisher.messages == []

    # Keep the physical ID stable while each displayed measurement changes.
    changed_payload = "b0e9fe5815cc1be406992e000302ff00"
    changed = co2_observed_packet(changed_payload)
    manager.record_advertisement(changed)
    assert manager.candidate_list()[0]["unconfirmed_preview"]["values"] == {
        "temperature_c": 25.6, "relative_humidity_percent": 46, "co2_ppm": 767,
    }
    request = dict(device_key=changed.device_key, sensor_id="test-co2", display_name="Test")
    with pytest.raises(ValueError):
        manager.register(request)
    registered = manager.register(dict(request, confirmed_model="co2_sensor"))
    assert registered.model == "co2_sensor"
    assert registry.list()[0].model == "co2_sensor"

    restarted = BleManager(SensorRegistry(registry.path), publisher)
    restarted._on_detection(SimpleNamespace(address=ADDRESS), SimpleNamespace(
        rssi=-50, manufacturer_data={sb.SWITCHBOT_COMPANY_ID: bytes.fromhex(changed_payload)}, service_data={}))
    assert restarted.observations[changed.device_key].model == "co2_sensor"
    assert restarted.registered_list()[0]["latest"]["values"] == {
        "temperature_c": 25.6, "relative_humidity_percent": 46, "co2_ppm": 767,
    }
    assert publisher.messages[-1][1]["co2_ppm"] == 767


@pytest.mark.parametrize("payload,service", [
    (CO2_OBSERVED[:-2], None),             # truncated manufacturer
    (CO2_OBSERVED[:16] + "0a992d003b02fe00", None),  # fractional digit > 9
    (CO2_OBSERVED[:20] + "65003b02fe00", None),  # relative humidity > 100
    (CO2_OBSERVED[:26] + "018f00", None),   # CO2 below 400 ppm
    (CO2_OBSERVED[:26] + "271100", None),   # CO2 above 10,000 ppm
    (CO2_OBSERVED, "540064009834"),          # explicit Meter
    (CO2_OBSERVED, "734064000002"),          # explicit Motion
    (IDENTITY + "3903009834", None),          # Meter manufacturer layout
    (IDENTITY + "376c0001", None),            # Motion manufacturer layout
])
def test_co2_preview_rejects_invalid_other_and_explicit_non_co2_frames(tmp_path, payload, service):
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    manager.scanning = True
    manager.record_advertisement(co2_observed_packet(payload, service))
    item = manager.candidate_list()[0]
    assert "co2_sensor" not in item.get("manual_registration_models", [])
    assert item.get("unconfirmed_preview", {}).get("model") != "co2_sensor"


def test_unconfirmed_options_are_unambiguous_for_presence_meter_and_co2():
    assert list(sb.unconfirmed_model_options(packet("208c0004008c", "0020640110ccc8"))) == ["presence_sensor"]
    assert list(sb.unconfirmed_model_options(packet("3903009834"))) == ["temperature_humidity_sensor"]
    assert list(sb.unconfirmed_model_options(co2_observed_packet())) == ["co2_sensor"]


def test_explicit_presence_type_remains_automatic_and_hint_conflicts_remain_unknown():
    result = packet("208c0004008c", "7020640110ccc8")
    assert result.model == "presence_sensor"
    assert result.raw["classification"]["evidence"] == "service_type"
    assert sb.unconfirmed_registration_candidate(result, "presence_sensor") is None
    unknown(packet("208c0004008c", "7020640110ccc8", "plug_sensor"))
    unknown(packet("208c0004008c", "6a0064", "presence_sensor"))
    md, _ = fields("208c0004008c", None)
    duplicate = {sb.METER_SERVICE_UUID: bytes.fromhex("0020640110ccc8"), sb.METER_SERVICE_UUID.upper(): bytes.fromhex("7020640110ccc8")}
    assert sb.unconfirmed_registration_candidate(sb.decode(ADDRESS, -50, md, duplicate, NOW), "presence_sensor") is None
