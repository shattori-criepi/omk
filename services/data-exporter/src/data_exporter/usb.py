"""Safe, reusable USB export backend.  It never formats or repairs media."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import logging
import time
from typing import Callable, Iterable

from .engine import ExportError, ExportResult, export_parquet


MOUNT_POINT = Path("/run/omk-export-usb")
USB_FILESYSTEMS = frozenset({"vfat", "exfat"})
UNMOUNT_VERIFICATION_ATTEMPTS = 10
UNMOUNT_VERIFICATION_INTERVAL_SECONDS = 0.25
LOGGER = logging.getLogger(__name__)


class UsbExportError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class UsbDevice:
    device: str
    filesystem: str
    label: str | None
    size: str | int | None
    mount_point: Path | None
    identity: str = ""


@dataclass(frozen=True)
class UsbStatus:
    state: str
    device: str | None = None
    filesystem: str | None = None
    label: str | None = None
    size: str | int | None = None
    free_space: int | None = None
    mount_state: str = "unmounted"
    mount_point: str | None = None
    identity: str | None = None

    def as_dict(self) -> dict[str, object | None]:
        return asdict(self)


class UsbLocator:
    """Find exactly one supported USB filesystem from lsblk's device tree."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._runner = runner

    def status(self) -> UsbStatus:
        result = self._runner(
            ["lsblk", "--json", "--bytes", "-o", "NAME,PATH,TYPE,TRAN,FSTYPE,LABEL,SIZE,MOUNTPOINT,SERIAL,WWN,UUID,PARTUUID"],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise UsbExportError("lsblk_failed", result.stderr.strip() or "lsblk failed")
        nodes = json.loads(result.stdout).get("blockdevices", [])
        supported_count = _supported_usb_filesystem_count(nodes)
        if supported_count > 1:
            return UsbStatus("ambiguous")
        candidates = _usb_candidates(nodes)
        if not candidates:
            if supported_count:
                return UsbStatus("identity_unavailable")
            return UsbStatus("unsupported_filesystem") if _has_unsupported_usb_filesystem(nodes) else UsbStatus("not_present")
        if len(candidates) > 1:
            return UsbStatus("ambiguous")
        candidate = candidates[0]
        free_space = None
        mount_state = "mounted" if candidate.mount_point else "unmounted"
        if candidate.mount_point:
            try:
                free_space = shutil.disk_usage(candidate.mount_point).free
            except OSError:
                pass
        return UsbStatus("mounted" if candidate.mount_point else "available", candidate.device, candidate.filesystem,
                         candidate.label, candidate.size, free_space, mount_state,
                         str(candidate.mount_point) if candidate.mount_point else None, candidate.identity)

    def one_exportable(self) -> UsbDevice:
        status = self.status()
        if status.state not in {"available", "mounted"}:
            raise UsbExportError(status.state)
        assert status.device and status.filesystem and status.identity
        mount_point = Path(status.mount_point) if status.mount_point else None
        if mount_point and not os.access(mount_point, os.W_OK):
            raise UsbExportError("mount_not_writable")
        return UsbDevice(status.device, status.filesystem, status.label, status.size, mount_point, status.identity)


def _usb_candidates(nodes: list[dict[str, object]], inherited_transport: str | None = None,
                    parent_serial: str | None = None, parent_wwn: str | None = None) -> list[UsbDevice]:
    candidates: list[UsbDevice] = []
    for node in nodes:
        transport = str(node.get("tran") or inherited_transport or "")
        serial = _optional_string(node.get("serial")) or parent_serial
        wwn = _optional_string(node.get("wwn")) or parent_wwn
        path = node.get("path")
        filesystem = str(node.get("fstype") or "").lower()
        mount = node.get("mountpoint")
        if transport == "usb" and filesystem in USB_FILESYSTEMS and isinstance(path, str) and mount != "/":
            identity = _identity_token(serial, wwn, _optional_string(node.get("uuid")), _optional_string(node.get("partuuid")))
            if identity:
                candidates.append(UsbDevice(path, filesystem, _optional_string(node.get("label")), node.get("size"),
                                            Path(mount) if isinstance(mount, str) and mount else None, identity))
        children = node.get("children")
        if isinstance(children, list):
            candidates.extend(_usb_candidates(children, transport, serial, wwn))
    return candidates


def _has_unsupported_usb_filesystem(nodes: list[dict[str, object]], inherited_transport: str | None = None) -> bool:
    for node in nodes:
        transport = str(node.get("tran") or inherited_transport or "")
        filesystem = str(node.get("fstype") or "").lower()
        if transport == "usb" and filesystem and filesystem not in USB_FILESYSTEMS:
            return True
        children = node.get("children")
        if isinstance(children, list) and _has_unsupported_usb_filesystem(children, transport):
            return True
    return False


def _supported_usb_filesystem_count(nodes: list[dict[str, object]], inherited_transport: str | None = None) -> int:
    count = 0
    for node in nodes:
        transport = str(node.get("tran") or inherited_transport or "")
        if transport == "usb" and str(node.get("fstype") or "").lower() in USB_FILESYSTEMS:
            count += 1
        children = node.get("children")
        if isinstance(children, list):
            count += _supported_usb_filesystem_count(children, transport)
    return count


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _identity_token(serial: str | None, wwn: str | None, uuid: str | None, partuuid: str | None) -> str | None:
    """Return an opaque token from stable block identities, never from paths or labels."""
    attributes = (("serial", serial), ("wwn", wwn), ("uuid", uuid), ("partuuid", partuuid))
    canonical = "|".join(f"{name}={value}" for name, value in attributes if value)
    return hashlib.sha256(canonical.encode()).hexdigest() if canonical else None


class UsbExportService:
    """Coordinate privileged mount actions and the existing unprivileged engine."""

    def __init__(self, processed_root: Path, locator: UsbLocator, helper: Callable[[str, str], None],
                 exporter: Callable[..., ExportResult] = export_parquet, sync: Callable[[], None] = os.sync,
                 lock_path: Path | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._processed_root = processed_root
        self._locator = locator
        self._helper = helper
        self._exporter = exporter
        self._sync = sync
        self._lock_path = lock_path or processed_root.parent / ".omk-export-usb.lock"
        self._sleep = sleep

    def export(self, from_date: date, to_date: date, datasets: Iterable[str] | None = None,
               expected_identity: str | None = None) -> ExportResult:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UsbExportError("busy") from error
            return self._export_locked(from_date, to_date, datasets, expected_identity)

    def _export_locked(self, from_date: date, to_date: date, datasets: Iterable[str] | None,
                       expected_identity: str | None) -> ExportResult:
        device = self._locator.one_exportable()
        identity = device.identity
        if not identity or (expected_identity is not None and expected_identity != identity):
            raise UsbExportError("usb_changed")
        mounted = device.mount_point is not None
        try:
            if not mounted:
                self._helper("mount", identity)
                mounted = True
                status = self._locator.status()
                if status.state != "mounted" or status.mount_point != str(MOUNT_POINT) or status.identity != identity:
                    raise UsbExportError("usb_changed")
                output_root = MOUNT_POINT
            else:
                output_root = device.mount_point
            assert output_root is not None
            self._require_identity(identity)
            output = output_root / "OMK"
            try:
                output.mkdir(exist_ok=True)
            except OSError as error:
                raise UsbExportError("output_directory_failed", str(error)) from error
            try:
                result = self._exporter(self._processed_root, output, from_date, to_date, datasets)
            except ExportError as error:
                raise UsbExportError("export_failed", str(error)) from error
            try:
                self._sync()
            except OSError as error:
                raise UsbExportError("sync_failed", str(error)) from error
        except Exception:
            if mounted:
                self._unmount_after_failure(identity)
            raise
        try:
            self._require_identity(identity)
        except UsbExportError as error:
            if error.code == "usb_changed":
                raise
            raise UsbExportError("unmount_failed", str(error)) from error
        try:
            self._helper("unmount", identity)
        except Exception as error:
            LOGGER.warning("USB export unmount helper failed")
            raise UsbExportError("unmount_failed", str(error)) from error
        if not self._wait_for_unmount():
            LOGGER.warning("USB remained mounted after unmount verification")
            raise UsbExportError("unmount_failed")
        return result

    def _wait_for_unmount(self) -> bool:
        """Absorb the short lsblk/systemd state propagation delay after unmount."""
        for attempt in range(UNMOUNT_VERIFICATION_ATTEMPTS):
            if self._locator.status().state != "mounted":
                return True
            if attempt + 1 < UNMOUNT_VERIFICATION_ATTEMPTS:
                self._sleep(UNMOUNT_VERIFICATION_INTERVAL_SECONDS)
        return False

    def _unmount_after_failure(self, identity: str) -> None:
        try:
            self._helper("unmount", identity)
        except Exception:
            pass

    def _require_identity(self, identity: str) -> None:
        try:
            current = self._locator.one_exportable()
        except UsbExportError as error:
            raise UsbExportError("usb_changed") from error
        if current.identity != identity:
            raise UsbExportError("usb_changed")


def privileged_helper(helper_path: Path) -> Callable[[str, str], None]:
    def run(action: str, identity: str) -> None:
        if action not in {"mount", "unmount"}:
            raise ValueError("invalid helper action")
        if not _is_identity_token(identity):
            raise UsbExportError("usb_changed")
        result = subprocess.run(["sudo", "-n", str(helper_path), action, identity], check=False, capture_output=True, text=True)
        if result.returncode:
            if "usb_changed" in result.stderr or "usb_changed" in result.stdout:
                raise UsbExportError("usb_changed")
            raise UsbExportError(f"{action}_failed", result.stderr.strip() or result.stdout.strip())
    return run


def _is_identity_token(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
