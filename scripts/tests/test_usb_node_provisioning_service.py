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
NODE_ID = "9af9509eb8b6"


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
        def __init__(self, _device): attempts.append("open")
        def request(self, *_args):
            if len(attempts) == 1: raise TimeoutError()
            return {"status": "ok", "protocol_version": 1, "node_id": NODE_ID}
        def close(self): pass
    monkeypatch.setattr(MODULE, "SerialJson", FakeSerial)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _: None)
    assert MODULE.identify(DEVICE, 1) == {"status": "ok", "protocol_version": 1, "node_id": NODE_ID}
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
    other = "09dda0d5a8f2"
    other_device = "/dev/serial/by-id/usb-Espressif-other"
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: [(other_device, other_device), (DEVICE, DEVICE)])
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: {"node_id": other if device == other_device else NODE_ID})
    assert MODULE.find_usb_node_by_id(NODE_ID) == (DEVICE, NODE_ID)
    with pytest.raises(RuntimeError, match="not uniquely"):
        MODULE.find_usb_node_by_id("3df94c3f187e")


def test_find_usb_node_by_id_stops_after_target_without_unrelated_serial_timeout(monkeypatch) -> None:
    atom_by_id = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit-if00"
    unrelated = "/dev/ttyUSB0"
    monkeypatch.setattr(MODULE, "physical_usb_devices", lambda: [(atom_by_id, DEVICE), (unrelated, unrelated)])
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID})
    assert MODULE.find_usb_node_by_id(NODE_ID) == (atom_by_id, NODE_ID)
    assert identified == [atom_by_id]


def test_usb_candidates_skips_unrelated_ttyusb_and_prioritizes_espressif_by_id(monkeypatch) -> None:
    atom_by_id = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit-if00"
    generic_by_id = "/dev/serial/by-id/usb-FTDI_FT232-if00"
    other_acm = "/dev/ttyACM1"
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [generic_by_id, other_acm, "/dev/ttyUSB0", atom_by_id, DEVICE])
    monkeypatch.setattr(MODULE, "canonical_device", lambda device: DEVICE if device in {atom_by_id, DEVICE} else device)
    identified = []
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: identified.append(device) or {"node_id": NODE_ID if device == atom_by_id else "09dda0d5a8f2"})
    assert MODULE.usb_candidates() == [
        {"device": atom_by_id, "node_id": NODE_ID, "wifi_configured": False},
        {"device": other_acm, "node_id": "09dda0d5a8f2", "wifi_configured": False},
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
    monkeypatch.setattr(MODULE, "provision", lambda *_args: NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args: called.append("mqtt"))
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
    monkeypatch.setattr(MODULE, "provision", lambda device, *_args: sent.append(device) or NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args: None)
    MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert sent == [by_id]
    with pytest.raises(MODULE.ProvisioningError, match="node_not_available"):
        MODULE.provision_selected_node("/dev/ttyACM9", NODE_ID)


def test_set_wifi_failure_and_mqtt_timeout_have_safe_codes_without_psk(monkeypatch, caplog) -> None:
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: [DEVICE])
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": DEVICE, "node_id": NODE_ID}])
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: ("omk", "secret-canary"))
    monkeypatch.setattr(MODULE, "provision", lambda *_args: (_ for _ in ()).throw(OSError("serial failure")))
    with pytest.raises(MODULE.ProvisioningError, match="set_wifi_failed"):
        MODULE.provision_selected_node(DEVICE, NODE_ID)
    monkeypatch.setattr(MODULE, "provision", lambda *_args: NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args: (_ for _ in ()).throw(MODULE.ProvisioningError("mqtt_registration_timeout")))
    with pytest.raises(MODULE.ProvisioningError, match="mqtt_registration_timeout"):
        MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert "secret-canary" not in caplog.text
