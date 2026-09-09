"""Bounded, testable recovery policy for a stopped RS-WSUHA-P USB adapter."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
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
TRUSTED_IDENTITY_PATH = Path("/etc/omk/broute-usb-recovery.conf")
SERIAL_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"


@dataclass(frozen=True, slots=True)
class UsbDevice:
    """Verified transport identity; privileged recovery additionally requires trust."""

    port_path: Path
    sysfs_path: Path
    vendor: str
    product: str
    serial: str


class UsbRecoveryError(RuntimeError):
    """A narrowly scoped RS-WSUHA-P recovery operation failed."""


def _check_root_owned(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = stat.S_IMODE(info.st_mode)
    valid_mode = mode == (0o755 if directory else 0o644)
    if (not valid_type or not valid_mode or info.st_uid != 0 or info.st_gid != 0
            or (not directory and info.st_nlink != 1)):
        raise UsbRecoveryError("Trusted USB identity must be root:root, directory 0755/file 0644, without links.")


def read_trusted_usb_serial() -> str:
    """Read administrator-established trust; missing/unsafe identity disables reset."""
    path = TRUSTED_IDENTITY_PATH
    try:
        _check_root_owned(path.parent.parent, directory=True)
        _check_root_owned(path.parent, directory=True)
        _check_root_owned(path)
        with path.open("rb") as stream:
            value = stream.read(66).decode("ascii")
        if not re.fullmatch(SERIAL_PATTERN + r"\n", value):
            raise UsbRecoveryError("Trusted USB identity has an invalid format.")
        return value[:-1]
    except (OSError, UnicodeError) as exc:
        raise UsbRecoveryError("Trusted USB identity is unavailable; privileged USB reset is disabled.") from exc


def register_trusted_usb_adapter(before: UsbDevice, after: UsbDevice) -> None:
    """Admin-only commit after successful real protocol configuration, never via sudoers.

    The CLI must obtain these snapshots before/after successful application-layer
    verification. No serial/path registration argument is exposed to the service.
    """
    if os.geteuid() != 0:
        raise UsbRecoveryError("Trust registration requires administrator privileges.")
    if before != after or not re.fullmatch(SERIAL_PATTERN, after.serial):
        raise UsbRecoveryError("USB identity changed during adapter verification.")
    path = TRUSTED_IDENTITY_PATH
    temporary = None
    try:
        _check_root_owned(path.parent.parent, directory=True)
        if not path.parent.exists():
            path.parent.mkdir(mode=0o755)
            path.parent.chmod(0o755)
            os.chown(path.parent, 0, 0)
        _check_root_owned(path.parent, directory=True)
        if path.exists() or path.is_symlink():
            existing = read_trusted_usb_serial()
            if existing != after.serial:
                raise UsbRecoveryError("Another adapter is already trusted; administrator removal is required before replacement.")
            return
        fd, temporary = tempfile.mkstemp(prefix=".broute-usb-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o644)
            stream.write(after.serial + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Exclusive publication: never overwrite a concurrently registered identity.
        os.link(temporary, path)
    except OSError as exc:
        raise UsbRecoveryError("Could not register trusted USB identity.") from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink()


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
    """Resolve transport identity; this alone does NOT establish model trust.

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
        if ((vendor, product) != (RS_WSUHA_P_VENDOR, RS_WSUHA_P_PRODUCT)
                or not re.fullmatch(SERIAL_PATTERN, serial)):
            raise UsbRecoveryError("USB recovery transport identity is unverified.")
        matches = []
        for entry in sys_class_tty.glob("ttyUSB*"):
            parent = _usb_parent(entry, sys_root)
            serial_file = parent / "serial"
            if serial_file.is_file() and serial_file.read_text(encoding="ascii").rstrip("\n") == serial:
                matches.append(parent)
        if (matches != [candidate]
                or _matching_usb_parents(serial, sys_root) != [candidate]):
            raise UsbRecoveryError("USB recovery serial identity is ambiguous.")
        return UsbDevice(port_path, candidate, vendor, product, serial)
    except (OSError, ValueError, UnicodeError) as exc:
        raise UsbRecoveryError("Cannot verify USB recovery identity from the selected port.") from exc


class UsbDeviceAbsent(UsbRecoveryError):
    """No matching USB parent, or its tty has not reappeared yet."""


class UsbResetAttemptError(UsbRecoveryError):
    """The guarded logical reset was invoked but did not complete."""


def _matching_usb_parents(serial: str, sys_root: Path) -> list[Path]:
    parents = []
    for entry in (sys_root / "bus/usb/devices").glob("*"):
        if not re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", entry.name):
            continue
        serial_file = entry / "serial"
        if serial_file.is_file() and serial_file.read_text(encoding="ascii").rstrip("\n") == serial:
            parent = entry.resolve(strict=True)
            if not parent.is_relative_to(sys_root / "devices") or parent.name != entry.name:
                raise UsbRecoveryError("Trusted USB parent is not canonical.")
            parents.append(parent)
    return parents


def resolve_trusted_usb(
    serial: str,
    preferred: Path,
    *,
    sys_class_tty: Path = Path("/sys/class/tty"),
    dev_root: Path = Path("/dev"),
) -> UsbDevice:
    """Find one trusted parent AND one tty, independently of the old port name.

    Count USB parents even without tty children: a duplicate serial must never
    become invisible merely because its driver has not bound yet.
    """
    sys_root = sys_class_tty.parent.parent
    try:
        parents = _matching_usb_parents(serial, sys_root)
        if not parents:
            raise UsbDeviceAbsent("Trusted USB adapter is absent.")
        if len(parents) != 1:
            raise UsbRecoveryError("Trusted USB identity is ambiguous.")
        parent = parents[0]
        transport = tuple((parent / field).read_text(encoding="ascii").strip().lower()
                          for field in ("idVendor", "idProduct"))
        if transport != (RS_WSUHA_P_VENDOR, RS_WSUHA_P_PRODUCT):
            raise UsbRecoveryError("Trusted USB transport guard mismatch.")
        ttys = [entry for entry in sys_class_tty.glob("ttyUSB*")
                if _usb_parent(entry, sys_root) == parent]
        if not ttys:
            raise UsbDeviceAbsent("Trusted USB tty has not reappeared.")
        if len(ttys) != 1:
            raise UsbRecoveryError("Trusted USB tty is ambiguous.")
        port = dev_root / ttys[0].name
        if not port.exists():
            raise UsbDeviceAbsent("Trusted USB device node has not reappeared.")
        device = resolve_rs_wsuha_p_usb(port, sys_class_tty=sys_class_tty, dev_root=dev_root)
        if device.serial != serial or device.sysfs_path != parent:
            raise UsbRecoveryError("Trusted USB identity changed during resolution.")
        # Prefer only a verified by-id alias. A stale alias never selects a device.
        aliases = [preferred] if preferred.parent == dev_root / "serial/by-id" else []
        aliases += sorted((dev_root / "serial/by-id").glob("*"))
        for alias in aliases:
            if alias.is_symlink() and alias.resolve() == port:
                return UsbDevice(alias, parent, device.vendor, device.product, serial)
        return device
    except (OSError, ValueError, UnicodeError) as exc:
        raise UsbRecoveryError("Cannot verify trusted USB correspondence.") from exc


class TrustedUsbPort(os.PathLike[str]):
    """Runtime identity, pinned at startup; every path use resolves it anew.

    This object is shared by presence waits and the transport's open callback,
    so neither retries nor PANA reconnection can fall back to the original tty.
    """

    def __init__(self, preferred: str) -> None:
        self.preferred = Path(preferred)
        self.serial = read_trusted_usb_serial()

    def __str__(self) -> str:
        return str(self.preferred)

    def __fspath__(self) -> str:
        return self.resolve()

    def resolve(self) -> str:
        if read_trusted_usb_serial() != self.serial:
            raise UsbRecoveryError("Trusted USB registration changed; restart required.")
        return str(resolve_trusted_usb(self.serial, self.preferred).port_path)


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
    """Check root-owned trust before invoking the no-argument privileged helper."""

    def __init__(self, port_path: Path, command: Path) -> None:
        self._port_path = port_path
        self._command = command

    def reset(self) -> UsbDevice:
        trusted_serial = read_trusted_usb_serial()
        device = resolve_rs_wsuha_p_usb(self._port_path)
        if device.serial != trusted_serial:
            raise UsbRecoveryError("Selected adapter does not match trusted USB identity.")
        try:
            subprocess.run(("sudo", "-n", str(self._command)), check=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UsbResetAttemptError("RS-WSUHA-P USB reset helper failed.") from exc
        for attempt in range(15):
            try:
                returned = resolve_trusted_usb(trusted_serial, self._port_path)
                break
            except UsbDeviceAbsent:
                if attempt == 14:
                    raise
                time.sleep(1)
        if returned.serial != trusted_serial:
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
