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
    monkeypatch.setattr(MODULE._core, "physical_usb_devices", lambda: [("/dev/ttyACM3", "/dev/ttyACM3"), ("/dev/ttyACM8", "/dev/ttyACM8")])
    monkeypatch.setattr(MODULE._core, "identify", lambda device, _timeout: (
        None if device.endswith("3") else {"status": "ok", "protocol_version": 2, "node_id": "020000000001"}
    ))
    assert MODULE.find_node(None, 1) == ("/dev/ttyACM8", "020000000001")


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
        stdout = "OMK-TEST\n"
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **kwargs: (
        captured.update(command=command, kwargs=kwargs) or Result()))
    assert MODULE.profile_value("omk-ap", "802-11-wireless.ssid") == "OMK-TEST"
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
              b'{"status":"ok","protocol_version":2,"node_id":"020000000001"}\n']
    serial = _serial_with_chunks(chunks)
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    monkeypatch.setattr(MODULE.select, "select", lambda *_args: ([42], [], []))
    monkeypatch.setattr(MODULE.os, "read", lambda *_args: chunks.pop(0))
    response = serial.request({"command": "identify", "protocol_version": 2}, 1,
                              lambda message: MODULE.is_protocol_response(message) and message["status"] == "ok")
    assert response["node_id"] == "020000000001"


def test_request_ignores_logs_until_set_wifi_response(monkeypatch) -> None:
    chunks = [b"I (100) wifi: state: init -> auth\n",
              b'{"status":"ok","protocol_version":2,"node_id":"020000000001"}\n',
              b'{"status":"accepted","protocol_version":2,"node_id":"020000000001"}\n']
    serial = _serial_with_chunks(chunks)
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    monkeypatch.setattr(MODULE.select, "select", lambda *_args: ([42], [], []))
    monkeypatch.setattr(MODULE.os, "read", lambda *_args: chunks.pop(0))
    response = serial.request({"command": "set_wifi", "protocol_version": 2}, 1,
                              lambda message: MODULE.is_protocol_response(message) and message["status"] == "accepted")
    assert response["status"] == "accepted"


def test_request_times_out_when_no_matching_protocol_response(monkeypatch) -> None:
    serial = _serial_with_chunks([])
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: 1)
    monkeypatch.setattr(MODULE.termios, "tcdrain", lambda *_args: None)
    with pytest.raises(TimeoutError, match="No OMK USB provisioning response"):
        serial.request({"command": "identify", "protocol_version": 2}, 0,
                       lambda message: MODULE.is_protocol_response(message) and message["status"] == "ok")


def test_provision_sends_versioned_request_without_printing_password(monkeypatch) -> None:
    captured: dict[str, object] = {}
    class FakeSerial:
        def __init__(self, _device: str) -> None: pass
        def request(self, request, _timeout, _matches):
            if request["command"] == "identify":
                return {"status": "ok", "protocol_version": 2, "node_id": "020000000001"}
            captured.update(request)
            return {"status": "accepted", "protocol_version": 2, "node_id": "020000000001"}
        def close(self): pass
    monkeypatch.setattr(MODULE._core, "SerialJson", FakeSerial)
    assert MODULE.provision("/dev/example", "omk", "synthetic-secret", 1, expected_node_id="020000000001") == "020000000001"
    assert captured == {"command": "set_wifi", "protocol_version": 2,
                        "ssid": "omk", "password": "synthetic-secret", "expected_node_id": "020000000001"}


def test_clear_wifi_sends_no_credential_and_accepts_only_success(monkeypatch) -> None:
    captured: dict[str, object] = {}
    class FakeSerial:
        def __init__(self, _device: str) -> None: pass
        def request(self, request, _timeout, _matches):
            if request["command"] == "identify":
                return {"status": "ok", "protocol_version": 2, "node_id": "020000000001"}
            captured.update(request)
            return {"status": "accepted", "protocol_version": 2, "node_id": "020000000001"}
        def close(self): pass
    monkeypatch.setattr(MODULE._core, "SerialJson", FakeSerial)
    assert MODULE.clear_wifi("/dev/example", 1, expected_node_id="020000000001") == "020000000001"
    assert captured == {"command": "clear_wifi", "protocol_version": 2, "expected_node_id": "020000000001"}


@pytest.mark.parametrize("status", ["busy", "storage_error", "restart_error", "invalid_request"])
def test_clear_wifi_preserves_safe_node_response_status(monkeypatch, status) -> None:
    class FakeSerial:
        def __init__(self, _device: str) -> None: pass
        def request(self, request, *_args):
            return {"status": "ok" if request["command"] == "identify" else status,
                    "protocol_version": 2, "node_id": "020000000001"}
        def close(self): pass
    monkeypatch.setattr(MODULE._core, "SerialJson", FakeSerial)
    with pytest.raises(MODULE.ProvisioningError, match=status):
        MODULE.clear_wifi("/dev/example", 1, expected_node_id="020000000001")


def test_clear_wifi_requires_explicit_device(monkeypatch) -> None:
    monkeypatch.setattr(MODULE.sys, "argv", ["provision_omk_node_via_usb.py", "--clear-wifi"])
    with pytest.raises(SystemExit) as error:
        MODULE.main()
    assert error.value.code == 2


def test_clear_wifi_identifies_same_explicit_node_before_and_after_reboot(monkeypatch, capsys) -> None:
    device = "/dev/serial/by-id/usb-omk-node"
    node_id = "020000000001"
    monkeypatch.setattr(MODULE.sys, "argv", ["provision_omk_node_via_usb.py", "--device", device, "--clear-wifi"])
    monkeypatch.setattr(MODULE, "find_node", lambda selected, _timeout: (selected, node_id))
    monkeypatch.setattr(MODULE, "clear_wifi", lambda selected, _timeout, **kwargs: node_id if selected == device else "other")
    after = []
    monkeypatch.setattr(MODULE, "wait_for_rebooted_identify", lambda selected, identified, _timeout: after.append((selected, identified)))
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda *_: (_ for _ in ()).throw(AssertionError("clear must not read PSK")))
    assert MODULE.main() == 0
    assert after == [(device, node_id)]
    assert node_id in capsys.readouterr().out


def test_clear_wifi_node_id_selects_only_the_identified_usb_node(monkeypatch, capsys) -> None:
    node_id = "020000000002"
    device = "/dev/serial/by-id/usb-omk-node"
    monkeypatch.setattr(MODULE.sys, "argv", ["provision_omk_node_via_usb.py", "--node-id", node_id, "--clear-wifi"])
    monkeypatch.setattr(MODULE, "find_usb_node_by_id", lambda requested, _timeout: (device, requested))
    monkeypatch.setattr(MODULE, "clear_wifi", lambda selected, _timeout, **kwargs: node_id if selected == device else "wrong-node")
    monkeypatch.setattr(MODULE, "wait_for_rebooted_identify", lambda *_args: None)
    assert MODULE.main() == 0
    assert node_id in capsys.readouterr().out


def test_registration_status_requires_matching_provisioned_node(monkeypatch) -> None:
    class FakeProcess:
        returncode = 0
        def communicate(self, timeout):
            assert timeout == 3
            return ('{"node_id":"020000000001","registration_state":"provisioned"}\n', "")
    monkeypatch.setattr(MODULE.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    MODULE.wait_for_registration_status("020000000001", "192.168.50.1", 1)


@pytest.mark.parametrize("ids", [[], ["020000000001"], ["020000000001", "020000000002"], ["020000000002", "020000000001"]])
def test_auto_selection_checks_all_physical_nodes(monkeypatch, ids):
    ports = [(f"/dev/ttyACM{i}", f"/dev/ttyACM{i}") for i in range(len(ids))]
    monkeypatch.setattr(MODULE._core, "physical_usb_devices", lambda: ports)
    checked = []
    monkeypatch.setattr(MODULE._core, "identify", lambda device, _: checked.append(device) or {"node_id": ids[int(device[-1])]})
    if len(ids) == 1:
        assert MODULE.find_node(None, 1) == (ports[0][0], ids[0])
    else:
        with pytest.raises(RuntimeError, match="No compatible|Multiple"):
            MODULE.find_node(None, 1)
    assert checked == [port for port, _ in ports]


def test_auto_selection_collapses_real_symlink_alias(monkeypatch, tmp_path):
    device = tmp_path / "tty"
    device.touch()
    alias = tmp_path / "by-id"
    alias.symlink_to(device)
    monkeypatch.setattr(MODULE._core, "candidate_devices", lambda: [str(alias), str(device)])
    monkeypatch.setattr(MODULE._core, "is_supported_device_group", lambda _: True)
    checked = []
    monkeypatch.setattr(MODULE._core, "identify", lambda device, _: checked.append(device) or {"node_id": "020000000001"})
    assert MODULE.find_node(None, 1)[1] == "020000000001"
    assert len(checked) == 1


@pytest.mark.parametrize("clear", [False, True])
def test_explicit_port_and_id_mismatch_stops_before_secrets_or_mutation(monkeypatch, clear):
    args = ["provision", "--device", "/dev/ttyACM0", "--node-id", "020000000001"]
    monkeypatch.setattr(MODULE.sys, "argv", args + (["--clear-wifi"] if clear else []))
    monkeypatch.setattr(MODULE, "identify", lambda *_: {"node_id": "020000000002"})
    def forbidden(*args, **kwargs): raise AssertionError("must not read credentials or mutate")
    for name in ("read_gateway_wifi", "provision", "clear_wifi"):
        monkeypatch.setattr(MODULE, name, forbidden)
    with pytest.raises(RuntimeError, match="does not match"):
        MODULE.main()


@pytest.mark.parametrize("selection", [[], ["--device", "/dev/ttyACM0"], ["--node-id", "020000000001"], ["--device", "/dev/ttyACM0", "--node-id", "020000000001"]])
def test_cli_passes_selected_identity_to_final_provision(monkeypatch, selection):
    monkeypatch.setattr(MODULE.sys, "argv", ["provision", *selection])
    monkeypatch.setattr(MODULE, "find_node", lambda *_: ("/dev/ttyACM0", "020000000001"))
    monkeypatch.setattr(MODULE, "find_usb_node_by_id", lambda *_: ("/dev/ttyACM0", "020000000001"))
    monkeypatch.setattr(MODULE, "read_gateway_wifi", lambda *_: ("OMK-TEST", "synthetic-secret"))
    sent = []
    monkeypatch.setattr(MODULE, "provision", lambda *args, **kwargs: sent.append(kwargs) or kwargs["expected_node_id"])
    monkeypatch.setattr(MODULE, "wait_for_registration_status", lambda *_: None)
    assert MODULE.main() == 0
    assert sent == [{"expected_node_id": "020000000001"}]
