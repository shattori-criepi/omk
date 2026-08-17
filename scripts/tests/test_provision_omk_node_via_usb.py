from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "provision_omk_node_via_usb.py"
SPEC = importlib.util.spec_from_file_location("provision_omk_node_via_usb", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_candidate_devices_deduplicates_existing_devices(monkeypatch) -> None:
    monkeypatch.setattr(MODULE.glob, "glob", lambda pattern: {
        "/dev/serial/by-id/*": ["/dev/a"], "/dev/ttyACM*": ["/dev/a", "/dev/b"],
        "/dev/ttyUSB*": [],
    }[pattern])
    monkeypatch.setattr(MODULE.os.path, "exists", lambda _path: True)
    assert MODULE.candidate_devices() == ["/dev/a", "/dev/b"]


def test_find_node_uses_identify_not_device_number(monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "candidate_devices", lambda: ["/dev/ttyACM3", "/dev/ttyACM8"])
    monkeypatch.setattr(MODULE, "identify", lambda device, _timeout: (
        None if device.endswith("3") else {"status": "ok", "protocol_version": 1, "node_id": "9af9509eb8b6"}
    ))
    assert MODULE.find_node(None, 1) == ("/dev/ttyACM8", "9af9509eb8b6")


def test_serial_open_matches_known_good_cdc_setup(monkeypatch) -> None:
    attributes = [1, 2, 3, 4, MODULE.termios.B9600, MODULE.termios.B9600, [0] * 32]
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(MODULE.os, "open", lambda *_args: 42)
    monkeypatch.setattr(MODULE.termios, "tcgetattr", lambda _fd: attributes)
    monkeypatch.setattr(MODULE.termios, "tcsetattr", lambda *args: calls.append(args))
    monkeypatch.setattr(MODULE.termios, "tcflush", lambda *args: calls.append(args))
    monkeypatch.setattr(MODULE.time, "sleep", lambda value: calls.append(("sleep", value)))
    serial = MODULE.SerialJson("/dev/ttyACM0")
    assert serial.fd == 42
    assert attributes[4:6] == [MODULE.termios.B115200, MODULE.termios.B115200]
    assert ("sleep", MODULE.SERIAL_SETTLE_SECONDS) in calls
    assert (42, MODULE.termios.TCIFLUSH) in calls


def test_read_profile_psk_elevates_only_the_nmcli_secret_query(monkeypatch) -> None:
    captured: dict[str, object] = {}
    class Result:
        stdout = "synthetic-secret\n"
    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    assert MODULE.read_profile_psk("omk-ap") == "synthetic-secret"
    assert captured["command"] == [
        "sudo", "--", "nmcli", "--show-secrets", "-g",
        "802-11-wireless-security.psk", "connection", "show", "omk-ap",
    ]
    assert captured["kwargs"] == {"check": True, "capture_output": True, "text": True}


def test_regular_profile_value_never_uses_sudo_or_show_secrets(monkeypatch) -> None:
    captured: dict[str, object] = {}
    class Result:
        stdout = "OMK-1CA9A8\n"
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **kwargs: (
        captured.update(command=command, kwargs=kwargs) or Result()))
    assert MODULE.profile_value("omk-ap", "802-11-wireless.ssid") == "OMK-1CA9A8"
    assert captured["command"] == ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "omk-ap"]


def _serial_with_chunks(chunks: list[bytes]) -> object:
    serial = MODULE.SerialJson.__new__(MODULE.SerialJson)
    serial.fd = 42
    serial.device = "/dev/ttyACM0"
    serial._buffer = b""
    return serial


def test_request_ignores_logs_malformed_and_unrelated_json_until_identify(monkeypatch) -> None:
    chunks = [b"I (99) omk_discovery_ble: SwitchBot advertisement\n",
              b"{not-json}\n", b'{"event":"boot"}\n',
              b'{"status":"ok","protocol_version":1,"node_id":"9af9509eb8b6"}\n']
    serial = _serial_with_chunks(chunks)
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    monkeypatch.setattr(MODULE.select, "select", lambda *_args: ([42], [], []))
    monkeypatch.setattr(MODULE.os, "read", lambda *_args: chunks.pop(0))
    response = serial.request({"command": "identify", "protocol_version": 1}, 1,
                              lambda message: MODULE.is_protocol_response(message) and message["status"] == "ok")
    assert response["node_id"] == "9af9509eb8b6"


def test_request_ignores_logs_until_set_wifi_response(monkeypatch) -> None:
    chunks = [b"I (100) wifi: state: init -> auth\n",
              b'{"status":"ok","protocol_version":1,"node_id":"9af9509eb8b6"}\n',
              b'{"status":"accepted","protocol_version":1,"node_id":"9af9509eb8b6"}\n']
    serial = _serial_with_chunks(chunks)
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    monkeypatch.setattr(MODULE.select, "select", lambda *_args: ([42], [], []))
    monkeypatch.setattr(MODULE.os, "read", lambda *_args: chunks.pop(0))
    response = serial.request({"command": "set_wifi", "protocol_version": 1}, 1,
                              lambda message: MODULE.is_protocol_response(message) and message["status"] == "accepted")
    assert response["status"] == "accepted"


def test_request_times_out_when_no_matching_protocol_response(monkeypatch) -> None:
    serial = _serial_with_chunks([])
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    with pytest.raises(TimeoutError, match="No OMK USB provisioning response"):
        serial.request({"command": "identify", "protocol_version": 1}, 0,
                       lambda message: MODULE.is_protocol_response(message) and message["status"] == "ok")


def test_provision_sends_versioned_request_without_printing_password(monkeypatch) -> None:
    captured: dict[str, object] = {}
    class FakeSerial:
        def __init__(self, _device: str) -> None: pass
        def request(self, request, _timeout, _matches):
            captured.update(request)
            return {"status": "accepted", "node_id": "9af9509eb8b6"}
        def close(self): pass
    monkeypatch.setattr(MODULE, "SerialJson", FakeSerial)
    assert MODULE.provision("/dev/example", "omk", "synthetic-secret", 1) == "9af9509eb8b6"
    assert captured == {"command": "set_wifi", "protocol_version": 1,
                        "ssid": "omk", "password": "synthetic-secret"}


def test_registration_status_requires_matching_provisioned_node(monkeypatch) -> None:
    class FakeProcess:
        returncode = 0
        def communicate(self, timeout):
            assert timeout == 3
            return ('{"node_id":"9af9509eb8b6","registration_state":"provisioned"}\n', "")
    monkeypatch.setattr(MODULE.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    MODULE.wait_for_registration_status("9af9509eb8b6", "192.168.50.1", 1)
