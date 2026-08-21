"""Bounded, testable recovery policy for a stopped RS-WSUHA-P USB adapter."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

RS_WSUHA_P_VENDOR = "0403"
RS_WSUHA_P_PRODUCT = "6015"
RS_WSUHA_P_SERIAL = "DM006AOS"
STARTUP_RETRY_ATTEMPTS = 3
USB_RESET_COOLDOWN = timedelta(minutes=20)
VBUS_CYCLE_COOLDOWN = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class UsbDevice:
    """Verified sysfs identity of the USB device eligible for recovery."""

    by_id_path: Path
    sysfs_path: Path
    vendor: str
    product: str
    serial: str


class UsbRecoveryError(RuntimeError):
    """A narrowly scoped RS-WSUHA-P recovery operation failed."""


def resolve_rs_wsuha_p_usb(
    by_id_path: Path,
    *,
    sys_class_tty: Path = Path("/sys/class/tty"),
) -> UsbDevice:
    """Resolve and verify the USB parent of a stable ``/dev/serial/by-id`` path."""

    resolved_port = by_id_path.resolve(strict=True)
    tty_name = resolved_port.name
    start = (sys_class_tty / tty_name / "device").resolve(strict=True)
    for candidate in (start, *start.parents):
        vendor_file = candidate / "idVendor"
        product_file = candidate / "idProduct"
        serial_file = candidate / "serial"
        if not (vendor_file.is_file() and product_file.is_file() and serial_file.is_file()):
            continue
        vendor = vendor_file.read_text(encoding="ascii").strip().lower()
        product = product_file.read_text(encoding="ascii").strip().lower()
        serial = serial_file.read_text(encoding="ascii").strip()
        if (vendor, product, serial) != (
            RS_WSUHA_P_VENDOR,
            RS_WSUHA_P_PRODUCT,
            RS_WSUHA_P_SERIAL,
        ):
            raise UsbRecoveryError(
                "RS-WSUHA-P USB identity does not match the approved FTDI device."
            )
        return UsbDevice(by_id_path, candidate, vendor, product, serial)
    raise UsbRecoveryError("Could not resolve a USB parent for the configured serial port.")


class RecoveryStateStore:
    """Persist only non-sensitive recovery status and last reset time."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, str]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) and all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ) else {}

    def write(self, status: str, *, now: datetime, reset_at: datetime | None = None, vbus_cycle_at: datetime | None = None) -> None:
        data = self.read()
        data["status"] = status
        data["updated_at"] = now.astimezone(UTC).isoformat()
        if reset_at is not None:
            data["last_usb_reset_at"] = reset_at.astimezone(UTC).isoformat()
        if vbus_cycle_at is not None:
            data["last_vbus_cycle_at"] = vbus_cycle_at.astimezone(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def cooldown_active(self, *, now: datetime) -> bool:
        raw = self.read().get("last_usb_reset_at")
        if raw is None:
            return False
        try:
            last_reset = datetime.fromisoformat(raw)
        except ValueError:
            return False
        return now.astimezone(UTC) < last_reset.astimezone(UTC) + USB_RESET_COOLDOWN

    def vbus_cooldown_active(self, *, now: datetime) -> bool:
        raw = self.read().get("last_vbus_cycle_at")
        if raw is None:
            return False
        try:
            last_cycle = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return False
        return now.astimezone(UTC) < last_cycle.astimezone(UTC) + VBUS_CYCLE_COOLDOWN


def usb_reset_allowed(
    consecutive_failures: int,
    resets_in_process: int,
    state: RecoveryStateStore,
    *,
    now: datetime,
) -> bool:
    """Return whether the bounded startup policy may request one USB reset."""

    return (
        consecutive_failures >= STARTUP_RETRY_ATTEMPTS
        and resets_in_process < 1
        and not state.cooldown_active(now=now)
    )


class RsWsuhaPUsbResetter:
    """Invoke the root-owned, no-argument reset helper after identity verification."""

    def __init__(self, by_id_path: Path, command: Path) -> None:
        self._by_id_path = by_id_path
        self._command = command

    def reset(self) -> UsbDevice:
        device = resolve_rs_wsuha_p_usb(self._by_id_path)
        try:
            subprocess.run(("sudo", "-n", str(self._command)), check=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UsbRecoveryError("RS-WSUHA-P USB reset helper failed.") from exc
        return device


class GatewayVbusCycler:
    """Run only the fixed, root-owned Pi 4 VBUS helper."""

    def __init__(self, command: Path) -> None:
        self._command = command

    def cycle(self) -> None:
        try:
            subprocess.run(("sudo", "-n", str(self._command)), check=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UsbRecoveryError("Gateway USB VBUS helper failed.") from exc
