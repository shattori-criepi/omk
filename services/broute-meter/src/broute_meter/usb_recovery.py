"""Bounded, testable recovery policy for a stopped RS-WSUHA-P USB adapter."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Additional guard for the observed transport, not an official RS-WSUHA-P VID/PID.
RS_WSUHA_P_VENDOR = "0403"
RS_WSUHA_P_PRODUCT = "6015"
STARTUP_RETRY_ATTEMPTS = 3
USB_RESET_COOLDOWN = timedelta(minutes=20)
VBUS_CYCLE_COOLDOWN = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class UsbDevice:
    """Verified sysfs identity of the USB device eligible for recovery."""

    port_path: Path
    sysfs_path: Path
    vendor: str
    product: str
    serial: str


class UsbRecoveryError(RuntimeError):
    """A narrowly scoped RS-WSUHA-P recovery operation failed."""


def _usb_parent(tty: Path, sys_root: Path) -> Path:
    start = (tty / "device").resolve(strict=True)
    if not start.is_relative_to(sys_root / "devices"):
        raise UsbRecoveryError("Serial port sysfs path is outside USB devices.")
    for candidate in (start, *start.parents):
        if not candidate.is_relative_to(sys_root / "devices"):
            break
        if (candidate / "idVendor").is_file() and (candidate / "idProduct").is_file():
            if not re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", candidate.name):
                break
            if (sys_root / "bus/usb/devices" / candidate.name).resolve(strict=True) != candidate:
                break
            return candidate
    raise UsbRecoveryError("Could not resolve a USB parent for the configured serial port.")


def _verify_tty_node(port: Path, tty: Path) -> None:
    info = port.stat()
    major, minor = map(int, (tty / "dev").read_text(encoding="ascii").strip().split(":"))
    if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.makedev(major, minor):
        raise UsbRecoveryError("Serial port is not the sysfs tty character device.")


def resolve_rs_wsuha_p_usb(
    port_path: Path,
    *,
    sys_class_tty: Path = Path("/sys/class/tty"),
    dev_root: Path = Path("/dev"),
) -> UsbDevice:
    """Verify the selected port (explicit or automatic) before privileged recovery.

    Linux pyserial reads USB product and serial from these same sysfs attributes.
    Generic FTDI descriptors alone cannot establish the B-route model identity.
    """
    try:
        resolved_port = port_path.resolve(strict=True)
        if resolved_port.parent != dev_root or not re.fullmatch(r"ttyUSB[0-9]+", resolved_port.name):
            raise UsbRecoveryError("Recovery requires a /dev/ttyUSB serial port.")
        sys_root = sys_class_tty.parent.parent
        tty = sys_class_tty / resolved_port.name
        _verify_tty_node(resolved_port, tty)
        candidate = _usb_parent(tty, sys_root)
        vendor = (candidate / "idVendor").read_text(encoding="ascii").strip().lower()
        product = (candidate / "idProduct").read_text(encoding="ascii").strip().lower()
        serial = (candidate / "serial").read_text(encoding="ascii").rstrip("\n")
        label = (candidate / "product").read_text(encoding="ascii").casefold()
        if ((vendor, product) != (RS_WSUHA_P_VENDOR, RS_WSUHA_P_PRODUCT)
                or "rs-wsuha-p" not in label
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", serial)):
            raise UsbRecoveryError("USB recovery model or transport identity is unverified.")
        matches = set()
        for entry in sys_class_tty.glob("ttyUSB*"):
            parent = _usb_parent(entry, sys_root)
            serial_file = parent / "serial"
            if serial_file.is_file() and serial_file.read_text(encoding="ascii").rstrip("\n") == serial:
                matches.add(parent)
        if matches != {candidate}:
            raise UsbRecoveryError("USB recovery serial identity is ambiguous.")
        return UsbDevice(port_path, candidate, vendor, product, serial)
    except (OSError, ValueError, UnicodeError) as exc:
        raise UsbRecoveryError("Cannot verify USB recovery identity from the selected port.") from exc


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
    """Pass only the verified serial to a helper that independently checks sysfs."""

    def __init__(self, port_path: Path, command: Path) -> None:
        self._port_path = port_path
        self._command = command

    def reset(self) -> UsbDevice:
        device = resolve_rs_wsuha_p_usb(self._port_path)
        try:
            subprocess.run(("sudo", "-n", str(self._command), device.serial), check=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UsbRecoveryError("RS-WSUHA-P USB reset helper failed.") from exc
        # devtmpfs may expose the tty before udev recreates its by-id symlink.
        # Wait only for missing nodes; never retry a different/ambiguous identity.
        for attempt in range(15):
            try:
                returned = resolve_rs_wsuha_p_usb(self._port_path)
                break
            except UsbRecoveryError as exc:
                if not isinstance(exc.__cause__, FileNotFoundError) or attempt == 14:
                    raise
                time.sleep(1)
        if (returned.vendor, returned.product, returned.serial) != (
            device.vendor, device.product, device.serial,
        ):
            raise UsbRecoveryError("USB identity changed after reset.")
        return returned


class GatewayVbusCycler:
    """Run only the fixed, root-owned Pi 4 VBUS helper."""

    def __init__(self, command: Path) -> None:
        self._command = command

    def cycle(self) -> None:
        try:
            subprocess.run(("sudo", "-n", str(self._command)), check=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UsbRecoveryError("Gateway USB VBUS helper failed.") from exc
