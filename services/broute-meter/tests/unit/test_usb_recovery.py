import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from broute_meter.usb_recovery import (
    RS_WSUHA_P_PRODUCT,
    RS_WSUHA_P_VENDOR,
    RecoveryStateStore,
    RsWsuhaPUsbResetter,
    UsbRecoveryError,
    resolve_rs_wsuha_p_usb,
    usb_reset_allowed,
)


def _usb_fixture(tmp_path: Path, *, vendor: str = RS_WSUHA_P_VENDOR,
                 serial: str = "TEST_ADAPTER_A", usb: str = "1-1.2",
                 tty_name: str = "ttyUSB9") -> tuple[Path, Path]:
    device = tmp_path / "sys" / "devices" / usb
    tty = device / f"{usb}:1.0" / tty_name
    tty.mkdir(parents=True)
    for name, value in (
        ("idVendor", vendor),
        ("idProduct", RS_WSUHA_P_PRODUCT),
        ("serial", serial),
        ("product", "FT230X Basic UART"),
    ):
        (device / name).write_text(value + "\n", encoding="ascii")
    resolved_port = tmp_path / "dev" / tty_name
    resolved_port.parent.mkdir(parents=True, exist_ok=True)
    resolved_port.touch()
    by_id = tmp_path / "dev" / "serial" / "by-id" / tty_name
    by_id.parent.mkdir(parents=True, exist_ok=True)
    by_id.symlink_to(resolved_port)
    class_tty = tmp_path / "sys" / "class" / "tty"
    (class_tty / tty_name).mkdir(parents=True)
    (class_tty / tty_name / "device").symlink_to(tty)
    bus = tmp_path / "sys/bus/usb/devices"
    bus.mkdir(parents=True, exist_ok=True)
    (bus / usb).symlink_to(device)
    return by_id, class_tty


@pytest.fixture
def resolve(monkeypatch, tmp_path):
    # Fake files cannot be mknod'ed on an unprivileged test host. The real
    # character-device guard is tested separately below, not removed in production.
    monkeypatch.setattr("broute_meter.usb_recovery._verify_tty_node", lambda *_: None)
    monkeypatch.setattr("broute_meter.usb_recovery.read_trusted_usb_serial", lambda: "TEST_ADAPTER_A")
    return lambda port: resolve_rs_wsuha_p_usb(
        port, sys_class_tty=tmp_path / "sys/class/tty", dev_root=tmp_path / "dev",
    )


@pytest.mark.parametrize("serial", ["TEST_ADAPTER_A", "TEST_ADAPTER_B"])
@pytest.mark.parametrize("explicit", [False, True])
def test_resolves_observed_transport_and_dynamic_serial(tmp_path, resolve, serial, explicit):
    by_id, _ = _usb_fixture(tmp_path, serial=serial)

    result = resolve(by_id.resolve() if explicit else by_id)

    assert result.vendor == RS_WSUHA_P_VENDOR
    assert result.product == RS_WSUHA_P_PRODUCT
    assert result.serial == serial


def test_rejects_non_matching_usb_identity(tmp_path: Path, resolve) -> None:
    by_id, _ = _usb_fixture(tmp_path, vendor="1234")

    with pytest.raises(UsbRecoveryError, match="identity"):
        resolve(by_id)


@pytest.mark.parametrize("field,value", [
    ("idProduct", "9999"), ("idVendor", "9999"),
    ("serial", "../bad"), ("serial", ""),
])
def test_explicit_port_does_not_bypass_identity(tmp_path, resolve, field, value):
    port, _ = _usb_fixture(tmp_path)
    (tmp_path / "sys/devices/1-1.2" / field).write_text(value)
    with pytest.raises(UsbRecoveryError):
        resolve(port.resolve())


def test_duplicate_serial_on_distinct_parents_is_rejected(tmp_path, resolve):
    port, _ = _usb_fixture(tmp_path)
    _usb_fixture(tmp_path, usb="1-1.3", tty_name="ttyUSB8")
    with pytest.raises(UsbRecoveryError, match="ambiguous"):
        resolve(port)


def test_missing_port_and_arbitrary_path_are_rejected(tmp_path, resolve):
    for port in (tmp_path / "absent", Path("/dev/null")):
        with pytest.raises(UsbRecoveryError):
            resolve(port)


def test_bad_sysfs_correspondence_is_rejected(tmp_path, resolve):
    port, _ = _usb_fixture(tmp_path)
    (tmp_path / "sys/bus/usb/devices/1-1.2").unlink()
    with pytest.raises(UsbRecoveryError):
        resolve(port)


def test_character_device_guard(tmp_path):
    from broute_meter.usb_recovery import _verify_tty_node
    (tmp_path / "dev").write_text("1:3\n")
    _verify_tty_node(Path("/dev/null"), tmp_path)
    with pytest.raises(UsbRecoveryError):
        _verify_tty_node(tmp_path / "dev", tmp_path)
    (tmp_path / "dev").write_text("1:5\n")
    with pytest.raises(UsbRecoveryError):
        _verify_tty_node(Path("/dev/null"), tmp_path)


def test_resetter_checks_trust_and_calls_no_argument_helper(tmp_path, resolve, monkeypatch):
    port, _ = _usb_fixture(tmp_path)
    device = resolve(port)
    monkeypatch.setattr("broute_meter.usb_recovery.resolve_rs_wsuha_p_usb", lambda _: device)
    run = Mock()
    monkeypatch.setattr("broute_meter.usb_recovery.subprocess.run", run)
    assert RsWsuhaPUsbResetter(port, Path("/root/helper")).reset() == device
    run.assert_called_once_with(
        ("sudo", "-n", "/root/helper"), check=True, timeout=45,
    )


def test_unverified_port_never_invokes_privileged_helper(monkeypatch):
    monkeypatch.setattr("broute_meter.usb_recovery.read_trusted_usb_serial", lambda: "TEST_ADAPTER_A")
    monkeypatch.setattr("broute_meter.usb_recovery.resolve_rs_wsuha_p_usb",
                        Mock(side_effect=UsbRecoveryError("Unverified model")))
    run = Mock()
    monkeypatch.setattr("broute_meter.usb_recovery.subprocess.run", run)
    with pytest.raises(UsbRecoveryError):
        RsWsuhaPUsbResetter(Path("/dev/ttyUSB9"), Path("/root/helper")).reset()
    run.assert_not_called()


@pytest.mark.parametrize("error", [OSError(), subprocess.CalledProcessError(1, "helper"),
                                  subprocess.TimeoutExpired("helper", 45)])
def test_helper_failure_becomes_recovery_error(tmp_path, resolve, monkeypatch, error):
    port, _ = _usb_fixture(tmp_path)
    device = resolve(port)
    monkeypatch.setattr("broute_meter.usb_recovery.resolve_rs_wsuha_p_usb", lambda _: device)
    monkeypatch.setattr("broute_meter.usb_recovery.subprocess.run", Mock(side_effect=error))
    with pytest.raises(UsbRecoveryError, match="helper failed"):
        RsWsuhaPUsbResetter(port, Path("/root/helper")).reset()


def test_resetter_rejects_changed_serial_after_helper(tmp_path, resolve, monkeypatch):
    from dataclasses import replace
    port, _ = _usb_fixture(tmp_path)
    device = resolve(port)
    monkeypatch.setattr("broute_meter.usb_recovery.resolve_rs_wsuha_p_usb",
                        Mock(side_effect=[device, replace(device, serial="TEST_OTHER")]))
    monkeypatch.setattr("broute_meter.usb_recovery.subprocess.run", Mock())
    with pytest.raises(UsbRecoveryError, match="changed after"):
        RsWsuhaPUsbResetter(port, Path("/root/helper")).reset()


@pytest.mark.parametrize("reappears", [True, False])
def test_resetter_waits_boundedly_for_udev_by_id(tmp_path, resolve, monkeypatch, reappears):
    port, _ = _usb_fixture(tmp_path)
    device = resolve(port)
    missing = UsbRecoveryError("Missing by-id")
    missing.__cause__ = FileNotFoundError()
    results = [device, missing, device] if reappears else [device] + [missing] * 15
    monkeypatch.setattr("broute_meter.usb_recovery.resolve_rs_wsuha_p_usb",
                        Mock(side_effect=results))
    monkeypatch.setattr("broute_meter.usb_recovery.subprocess.run", Mock())
    sleep = Mock()
    monkeypatch.setattr("broute_meter.usb_recovery.time.sleep", sleep)
    resetter = RsWsuhaPUsbResetter(port, Path("/root/helper"))
    if reappears:
        assert resetter.reset() == device
        sleep.assert_called_once_with(1)
    else:
        with pytest.raises(UsbRecoveryError):
            resetter.reset()
        assert sleep.call_count == 14


def test_state_store_enforces_persistent_cooldown(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path / "state.json")
    reset_at = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)

    store.write("usb_resetting", now=reset_at, reset_at=reset_at)

    assert store.cooldown_active(now=reset_at + timedelta(minutes=19))
    assert not store.cooldown_active(now=reset_at + timedelta(minutes=20))
    assert store.read()["status"] == "usb_resetting"


def test_usb_reset_policy_requires_repeated_failures_and_one_reset_per_process(
    tmp_path: Path,
) -> None:
    store = RecoveryStateStore(tmp_path / "state.json")
    now = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)

    assert not usb_reset_allowed(1, 0, store, now=now)
    assert usb_reset_allowed(3, 0, store, now=now)
    assert not usb_reset_allowed(3, 1, store, now=now)
    store.write("usb_resetting", now=now, reset_at=now)
    assert not usb_reset_allowed(3, 0, store, now=now)


def test_vbus_cooldown_is_persistent_for_one_hour_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    cycle_at = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    RecoveryStateStore(path).write("vbus_cycling", now=cycle_at, vbus_cycle_at=cycle_at)

    restarted_store = RecoveryStateStore(path)
    assert restarted_store.vbus_cooldown_active(now=cycle_at + timedelta(minutes=59, seconds=59))
    assert not restarted_store.vbus_cooldown_active(now=cycle_at + timedelta(hours=1))
