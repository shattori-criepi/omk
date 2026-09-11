from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "services/system-manager/src/omk_system_manager/node_provisioning.py"
SPEC = importlib.util.spec_from_file_location("node_provisioning_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DEVICE = "/dev/ttyACM0"
NODE_ID = "020000000001"


def test_usb_candidates_deduplicates_same_node_and_prefers_by_id(monkeypatch) -> None:
    by_id = "/dev/serial/by-id/usb-Espressif-if00"
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [by_id, DEVICE, "/dev/ttyUSB0"])
    monkeypatch.setattr(MODULE, "canonical_device", lambda device: DEVICE if device in {by_id, DEVICE} else device)
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID})
    assert MODULE.usb_candidates() == [{"device": by_id, "node_id": NODE_ID, "wifi_configured": False}]
    assert identified == [by_id]


def test_identify_retries_after_initial_timeout(monkeypatch) -> None:
    attempts = []
    class FakeSerial:
        def __init__(self, _device, **_kwargs): attempts.append("open")
        def request(self, *_args):
            if len(attempts) == 1: raise TimeoutError()
            return {"status": "ok", "protocol_version": 2, "node_id": NODE_ID}
        def close(self): pass
    monkeypatch.setattr(MODULE, "SerialJson", FakeSerial)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    assert MODULE.identify(DEVICE, 1) == {"status": "ok", "protocol_version": 2, "node_id": NODE_ID}
    assert attempts == ["open", "open"]


def test_by_id_alias_is_representative_and_identified_once(monkeypatch) -> None:
    by_id = "/dev/serial/by-id/usb-Espressif-if00"
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [by_id, DEVICE])
    monkeypatch.setattr(MODULE, "canonical_device", lambda _device: DEVICE)
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID})
    assert MODULE.usb_candidates() == [{"device": by_id, "node_id": NODE_ID, "wifi_configured": False}]
    assert identified == [by_id]


def test_find_usb_node_by_id_selects_only_requested_node(monkeypatch) -> None:
    other = "020000000002"
    other_device = "/dev/serial/by-id/usb-Espressif-other"
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: [(other_device, other_device), (DEVICE, DEVICE)])
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: {"node_id": other if device == other_device else NODE_ID})
    assert MODULE.find_usb_node_by_id(NODE_ID) == (DEVICE, NODE_ID)
    with pytest.raises(RuntimeError, match="not uniquely"):
        MODULE.find_usb_node_by_id("020000000003")


def test_find_usb_node_by_id_checks_all_physical_candidates(monkeypatch) -> None:
    atom_by_id = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit-if00"
    unrelated = "/dev/ttyUSB0"
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: [(atom_by_id, DEVICE), (unrelated, unrelated)])
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID if device == atom_by_id else "020000000002"})
    assert MODULE.find_usb_node_by_id(NODE_ID) == (atom_by_id, NODE_ID)
    assert identified == [atom_by_id, unrelated]


def test_usb_candidates_skips_unrelated_ttyusb_and_prioritizes_espressif_by_id(monkeypatch) -> None:
    atom_by_id = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit-if00"
    generic_by_id = "/dev/serial/by-id/usb-FTDI_FT232-if00"
    other_acm = "/dev/ttyACM1"
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [generic_by_id, other_acm, "/dev/ttyUSB0", atom_by_id, DEVICE])
    monkeypatch.setattr(MODULE, "canonical_device", lambda device: DEVICE if device in {atom_by_id, DEVICE} else device)
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID if device == atom_by_id else "020000000002"})
    assert MODULE.usb_candidates() == [
        {"device": atom_by_id, "node_id": NODE_ID, "wifi_configured": False},
        {"device": other_acm, "node_id": "020000000002", "wifi_configured": False},
    ]
    assert identified == [atom_by_id, other_acm]


def test_missing_node_scans_each_supported_physical_device_once(monkeypatch) -> None:
    first = "/dev/serial/by-id/usb-Espressif-first"
    second = "/dev/ttyACM1"
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: [(first, DEVICE), (second, second)])
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or None)
    with pytest.raises(RuntimeError, match="not uniquely"):
        MODULE.find_usb_node_by_id(NODE_ID, timeout=1)
    assert identified == [first, second]


def test_selected_node_rechecks_exact_device_and_identity_before_credentials(monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [DEVICE])
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": DEVICE, "node_id": NODE_ID}])
    called = []
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: called.append("credentials") or ("omk", "secret-canary"))
    monkeypatch.setattr(MODULE, "provision", lambda *_args, **_kwargs: NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args, **_kwargs: called.append("mqtt"))
    MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert called == ["credentials", "mqtt"]
    with pytest.raises(MODULE.ProvisioningError, match="node_not_available"):
        MODULE.provision_selected_node("/dev/ttyACM1", NODE_ID)


def test_selected_node_accepts_realpath_alias_but_rejects_different_physical_device(monkeypatch) -> None:
    by_id = "/dev/serial/by-id/usb-Espressif-if00"
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [by_id, DEVICE])
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": by_id, "node_id": NODE_ID}])
    monkeypatch.setattr(MODULE, "canonical_device", lambda device: DEVICE if device in {by_id, DEVICE} else device)
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: ("omk", "secret-canary"))
    sent = []
    monkeypatch.setattr(MODULE, "provision", lambda device, *_args, **_kwargs: sent.append(device) or NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args, **_kwargs: None)
    MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert sent == [by_id]
    with pytest.raises(MODULE.ProvisioningError, match="node_not_available"):
        MODULE.provision_selected_node("/dev/ttyACM9", NODE_ID)


def test_set_wifi_failure_and_mqtt_timeout_have_safe_codes_without_psk(monkeypatch, caplog) -> None:
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [DEVICE])
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": DEVICE, "node_id": NODE_ID}])
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: ("omk", "secret-canary"))
    monkeypatch.setattr(MODULE, "provision", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("serial failure")))
    with pytest.raises(MODULE.ProvisioningError, match="set_wifi_failed"):
        MODULE.provision_selected_node(DEVICE, NODE_ID)
    monkeypatch.setattr(MODULE, "provision", lambda *_args, **_kwargs: NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(MODULE.ProvisioningError("mqtt_registration_timeout")))
    with pytest.raises(MODULE.ProvisioningError, match="mqtt_registration_timeout"):
        MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert "secret-canary" not in caplog.text


@pytest.mark.parametrize("reverse", [False, True])
def test_distinct_ports_with_duplicate_node_id_are_rejected(monkeypatch, reverse):
    ports = [("/dev/ttyACM0", "/dev/ttyACM0"), ("/dev/ttyACM1", "/dev/ttyACM1")]
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: ports[::-1] if reverse else ports)
    monkeypatch.setattr(MODULE, "identify", lambda *_: {"node_id": NODE_ID})
    with pytest.raises(MODULE.ProvisioningError, match="ambiguous_node_identity"):
        MODULE.usb_candidates()
    with pytest.raises(MODULE.ProvisioningError, match="ambiguous_node_identity"):
        MODULE.find_usb_node_by_id(NODE_ID)


@pytest.mark.parametrize("command", ["set_wifi", "clear_wifi"])
@pytest.mark.parametrize("actual", ["020000000001", "020000000002", "legacy", "timeout"])
def test_mutation_identifies_same_open_connection_before_any_state_change(monkeypatch, command, actual):
    events, saved, cleared = [], [], []
    class Serial:
        def __init__(self, device): events.append(("open", device))
        def request(self, request, timeout, matches):
            events.append(request)
            if request["command"] == "identify":
                response = {"protocol_version": 1 if actual == "legacy" else 2,
                            "node_id": NODE_ID if actual == "legacy" else actual, "status": "ok"}
                if actual == "timeout" or not matches(response): raise TimeoutError()
                return response
            assert request["expected_node_id"] == NODE_ID
            (saved if request["command"] == "set_wifi" else cleared).append(request)
            response = {"protocol_version": 2, "node_id": NODE_ID, "status": "accepted"}
            assert matches(response)
            return response
        def close(self): events.append("close")
    monkeypatch.setattr(MODULE, "SerialJson", Serial)
    def mutate():
        if command == "set_wifi":
            return MODULE.provision(DEVICE, "OMK-TEST", "synthetic-secret", expected_node_id=NODE_ID)
        return MODULE.clear_wifi(DEVICE, expected_node_id=NODE_ID)
    if actual == NODE_ID:
        assert mutate() == NODE_ID
        assert len(saved) == (command == "set_wifi")
        assert len(cleared) == (command == "clear_wifi")
        assert [event["command"] for event in events if isinstance(event, dict)] == ["identify", command]
    else:
        with pytest.raises((MODULE.ProvisioningError, TimeoutError)):
            mutate()
        assert saved == [] and cleared == []
        assert [event["command"] for event in events if isinstance(event, dict)] == ["identify"]
        assert "synthetic-secret" not in str(events)
    assert events[0] == ("open", DEVICE) and events[-1] == "close"
    assert sum(isinstance(event, tuple) for event in events) == 1


def test_dashboard_reused_tty_after_discovery_never_receives_credentials(monkeypatch):
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [DEVICE])
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": DEVICE, "node_id": NODE_ID}])
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: ("OMK-TEST", "synthetic-secret"))
    requests, saved = [], []
    class ReplacementNode:
        def __init__(self, device): assert device == DEVICE
        def request(self, request, timeout, matches):
            requests.append(request)
            if request["command"] == "set_wifi": saved.append(request)
            return {"protocol_version": 2, "status": "ok", "node_id": "020000000002"}
        def close(self): pass
    monkeypatch.setattr(MODULE, "SerialJson", ReplacementNode)
    with pytest.raises(MODULE.ProvisioningError, match="node_identity_changed"):
        MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert saved == []
    assert requests == [{"command": "identify", "protocol_version": 2}]


def test_legacy_nodes_are_counted_in_inventory_but_never_provisioned(monkeypatch):
    ports = [("/dev/ttyACM0", "/dev/ttyACM0"), ("/dev/ttyACM1", "/dev/ttyACM1")]
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: ports)
    writes = []
    class Serial:
        def __init__(self, device, **_kwargs): self.legacy = device.endswith("0")
        def request(self, request, timeout, matches):
            writes.append(request)
            version = 1 if self.legacy else 2
            status = "invalid_request" if self.legacy and request["protocol_version"] == 2 else "ok"
            response = {"protocol_version": version, "status": status,
                        "node_id": NODE_ID if self.legacy else "020000000002"}
            assert matches(response)
            return response
        def close(self): pass
    monkeypatch.setattr(MODULE, "SerialJson", Serial)
    assert len(MODULE.usb_candidates()) == 2
    with pytest.raises(MODULE.ProvisioningError, match="unsupported_usb_protocol"):
        MODULE.provision(DEVICE, "OMK-TEST", "synthetic-secret", expected_node_id=NODE_ID)
    assert all(request["command"] == "identify" for request in writes)
    assert "synthetic-secret" not in str(writes)
