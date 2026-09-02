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
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: (
        {"node_id": NODE_ID} if device != "/dev/ttyUSB0" else None))
    assert MODULE.usb_candidates() == [{"device": by_id, "node_id": NODE_ID}]


def test_selected_node_rechecks_exact_device_and_identity_before_credentials(monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "usb_candidates", lambda: [{"device": DEVICE, "node_id": NODE_ID}])
    called = []
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda: called.append("credentials") or ("omk", "secret-canary"))
    monkeypatch.setattr(MODULE, "provision", lambda *_args: NODE_ID)
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_args: called.append("mqtt"))
    MODULE.provision_selected_node(DEVICE, NODE_ID)
    assert called == ["credentials", "mqtt"]
    with pytest.raises(MODULE.ProvisioningError, match="node_not_available"):
        MODULE.provision_selected_node("/dev/ttyACM1", NODE_ID)


def test_set_wifi_failure_and_mqtt_timeout_have_safe_codes_without_psk(monkeypatch, caplog) -> None:
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
