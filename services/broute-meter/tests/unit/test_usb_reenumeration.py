"""Identity tracking uses fake sysfs; serial I/O is recorded, never hardware."""

import shutil
from pathlib import Path

import pytest
from test_serial_transport import FakeSerial, RecordingFactory
from test_usb_recovery import _usb_fixture

from broute_meter import cli
from broute_meter import usb_recovery as usb
from broute_meter.serial.transport import PySerialTransport


@pytest.fixture
def tree(tmp_path, monkeypatch):
    real_resolver = usb.resolve_trusted_usb
    monkeypatch.setattr(usb, "read_trusted_usb_serial", lambda: "TEST_ADAPTER_A")
    monkeypatch.setattr(usb, "_verify_tty_node", lambda *_: None)
    monkeypatch.setattr(
        usb,
        "resolve_trusted_usb",
        lambda serial, preferred: real_resolver(
            serial, preferred, sys_class_tty=tmp_path / "sys/class/tty", dev_root=tmp_path / "dev"
        ),
    )
    return tmp_path


def remove(tree, tty="ttyUSB0", parent="1-1.2"):
    shutil.rmtree(tree / "sys/class/tty" / tty)
    (tree / "dev" / tty).unlink()
    (tree / "sys/bus/usb/devices" / parent).unlink()
    shutil.rmtree(tree / "sys/devices" / parent)


def tracked(tree, preferred):
    return usb.TrustedUsbPort(str(preferred))


@pytest.mark.parametrize("by_id", [False, True])
def test_reopen_follows_serial_when_old_tty_is_reassigned(tree, by_id):
    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    original = alias if by_id else tree / "dev/ttyUSB0"
    port = tracked(tree, original)
    connection = FakeSerial()
    factory = RecordingFactory(connection)
    transport = PySerialTransport(
        port=str(original),
        baudrate=115200,
        timeout_seconds=1,
        serial_factory=factory,
        port_resolver=port.resolve,
    )
    transport.open()
    transport.write(b"before")
    transport.close()
    remove(tree)
    alias.unlink()
    _usb_fixture(tree, tty_name="ttyUSB0", usb="1-1.3", serial="TEST_OTHER")
    new_alias, _ = _usb_fixture(tree, tty_name="ttyUSB1")
    # udev has not generated the new by-id yet; the stale old alias is untrusted.
    new_alias.unlink()
    assert cli._adapter_device_present(port)
    assert Path(port) == tree / "dev/ttyUSB1"
    transport.open()
    transport.write(b"after")
    assert factory.calls[-1]["port"] == str(tree / "dev/ttyUSB1")
    assert connection.written_data == [b"before", b"after"]
    transport.close()
    new_alias.symlink_to(tree / "dev/ttyUSB1")
    transport.open()
    assert factory.calls[-1]["port"] == str(new_alias)


def test_missing_wait_does_not_adopt_other_transport(tree, monkeypatch):
    _usb_fixture(tree, tty_name="ttyUSB0", usb="1-1.3", serial="TEST_OTHER")
    port = tracked(tree, tree / "dev/ttyUSB0")
    assert not cli._adapter_device_present(port)
    monkeypatch.setattr(cli, "VBUS_DEVICE_REAPPEAR_TIMEOUT_SECONDS", 2)
    waits = []
    clock = iter([0, 0, 1, 2])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))

    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return False

    assert not cli._wait_for_vbus_device(port, Stop())
    assert len(waits) == 2


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_parent",
        "duplicate_unbound",
        "bad_transport",
        "bad_bus",
        "bad_tty",
        "duplicate_tty",
    ],
)
def test_ambiguity_or_contradiction_never_opens_serial(tree, problem):
    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    if problem.startswith("duplicate_") and problem != "duplicate_tty":
        _usb_fixture(tree, tty_name="ttyUSB1", usb="1-1.3")
        if problem == "duplicate_unbound":
            shutil.rmtree(tree / "sys/class/tty/ttyUSB1")
    elif problem == "duplicate_tty":
        entry = tree / "sys/class/tty/ttyUSB1"
        entry.mkdir()
        (entry / "device").symlink_to((tree / "sys/class/tty/ttyUSB0/device").resolve())
    elif problem == "bad_transport":
        (tree / "sys/devices/1-1.2/idProduct").write_text("9999")
    elif problem == "bad_bus":
        link = tree / "sys/bus/usb/devices/1-1.2"
        link.unlink()
        link.symlink_to(tree)
    else:
        (tree / "sys/class/tty/ttyUSB0/device").unlink()
    port = tracked(tree, alias)
    factory = RecordingFactory(FakeSerial())
    transport = PySerialTransport(
        port=str(alias),
        baudrate=115200,
        timeout_seconds=1,
        serial_factory=factory,
        port_resolver=port.resolve,
    )
    with pytest.raises(usb.UsbRecoveryError):
        transport.open()
    assert not factory.calls


def test_identity_changes_during_open_closes_without_writes(tree):
    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    port = tracked(tree, alias)
    connection = FakeSerial()

    def factory(**kwargs):
        (tree / "sys/devices/1-1.2/serial").write_text("TEST_OTHER")
        return connection

    transport = PySerialTransport(
        port=str(alias),
        baudrate=115200,
        timeout_seconds=1,
        serial_factory=factory,
        port_resolver=port.resolve,
    )
    with pytest.raises(usb.UsbRecoveryError):
        transport.open()
    assert not connection.is_open
    assert not connection.written_data


def test_vbus_wait_revalidates_trust(tree):
    _usb_fixture(tree, tty_name="ttyUSB0", usb="1-1.3", serial="TEST_OTHER")
    port = tracked(tree, tree / "dev/ttyUSB0")

    class Stop:
        def wait(self, seconds):
            _usb_fixture(tree, tty_name="ttyUSB1")
            return False

    assert cli._wait_for_vbus_device(port, Stop())
    assert Path(port).resolve() == tree / "dev/ttyUSB1"


def test_root_registration_change_requires_restart(tree, monkeypatch):
    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    port = tracked(tree, alias)
    monkeypatch.setattr(usb, "read_trusted_usb_serial", lambda: "TEST_OTHER")
    with pytest.raises(usb.UsbRecoveryError, match="restart"):
        port.resolve()


def test_runtime_adapter_uses_resolver_for_every_open(tree, monkeypatch):
    # Exercise the actual CLI factory and adapter object, not only the resolver.
    from broute_meter.config import AppConfig

    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    port = tracked(tree, tree / "dev/ttyUSB0")
    factory = RecordingFactory(FakeSerial())
    monkeypatch.setattr("broute_meter.serial.transport.pyserial.Serial", factory)
    adapter = cli._create_rs_wsuha_p_adapter(AppConfig(), port)
    adapter.open()
    adapter.close()
    remove(tree)
    alias.unlink()
    _usb_fixture(tree, tty_name="ttyUSB0", usb="1-1.3", serial="TEST_OTHER")
    _usb_fixture(tree, tty_name="ttyUSB1")
    adapter.open()
    assert Path(factory.calls[-1]["port"]).resolve() == tree / "dev/ttyUSB1"
    adapter.close()


def test_real_resetter_re_resolves_after_helper_changes_enumeration(tree, monkeypatch):
    alias, _ = _usb_fixture(tree, tty_name="ttyUSB0")
    original = usb.resolve_rs_wsuha_p_usb
    monkeypatch.setattr(
        usb,
        "resolve_rs_wsuha_p_usb",
        lambda port, **kwargs: original(
            port, sys_class_tty=tree / "sys/class/tty", dev_root=tree / "dev"
        ),
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        remove(tree)
        alias.unlink()
        _usb_fixture(tree, tty_name="ttyUSB0", usb="1-1.3", serial="TEST_OTHER")
        by_id, _ = _usb_fixture(tree, tty_name="ttyUSB1")
        by_id.unlink()  # tty precedes udev's stable alias

    monkeypatch.setattr(usb.subprocess, "run", run)
    result = usb.RsWsuhaPUsbResetter(tree / "dev/ttyUSB0", Path("/root/fake-helper")).reset()
    assert result.serial == "TEST_ADAPTER_A"
    assert result.port_path == tree / "dev/ttyUSB1"
    assert calls == [("sudo", "-n", "/root/fake-helper")]
