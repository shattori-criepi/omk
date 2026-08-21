from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from broute_meter.usb_recovery import (
    RS_WSUHA_P_PRODUCT,
    RS_WSUHA_P_SERIAL,
    RS_WSUHA_P_VENDOR,
    RecoveryStateStore,
    UsbRecoveryError,
    resolve_rs_wsuha_p_usb,
    usb_reset_allowed,
)


def _usb_fixture(tmp_path: Path, *, vendor: str = RS_WSUHA_P_VENDOR) -> tuple[Path, Path]:
    device = tmp_path / "sys" / "devices" / "1-1.2"
    tty = device / "1-1.2:1.0" / "ttyUSB9"
    tty.mkdir(parents=True)
    for name, value in (
        ("idVendor", vendor),
        ("idProduct", RS_WSUHA_P_PRODUCT),
        ("serial", RS_WSUHA_P_SERIAL),
    ):
        (device / name).write_text(value + "\n", encoding="ascii")
    resolved_port = tmp_path / "dev" / "ttyUSB9"
    resolved_port.parent.mkdir(parents=True)
    resolved_port.touch()
    by_id = tmp_path / "dev" / "serial" / "by-id" / "approved"
    by_id.parent.mkdir(parents=True)
    by_id.symlink_to(resolved_port)
    class_tty = tmp_path / "sys" / "class" / "tty"
    (class_tty / "ttyUSB9").mkdir(parents=True)
    (class_tty / "ttyUSB9" / "device").symlink_to(tty)
    return by_id, class_tty


def test_resolves_only_approved_ftdi_usb_device(tmp_path: Path) -> None:
    by_id, class_tty = _usb_fixture(tmp_path)

    result = resolve_rs_wsuha_p_usb(by_id, sys_class_tty=class_tty)

    assert result.vendor == RS_WSUHA_P_VENDOR
    assert result.product == RS_WSUHA_P_PRODUCT
    assert result.serial == RS_WSUHA_P_SERIAL


def test_rejects_non_matching_usb_identity(tmp_path: Path) -> None:
    by_id, class_tty = _usb_fixture(tmp_path, vendor="1234")

    with pytest.raises(UsbRecoveryError, match="identity"):
        resolve_rs_wsuha_p_usb(by_id, sys_class_tty=class_tty)


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
