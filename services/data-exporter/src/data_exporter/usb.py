"""Safe, reusable USB export backend.  It never formats or repairs media."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Iterable

from .engine import ExportError, ExportResult, export_parquet


MOUNT_POINT = Path("/run/omk-export-usb")
USB_FILESYSTEMS = frozenset({"vfat", "exfat"})


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

    def as_dict(self) -> dict[str, object | None]:
        return asdict(self)


class UsbLocator:
    """Find exactly one supported USB filesystem from lsblk's device tree."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._runner = runner

    def status(self) -> UsbStatus:
        result = self._runner(
            ["lsblk", "--json", "--bytes", "-o", "NAME,PATH,TYPE,TRAN,FSTYPE,LABEL,SIZE,MOUNTPOINT"],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise UsbExportError("lsblk_failed", result.stderr.strip() or "lsblk failed")
        candidates = _usb_candidates(json.loads(result.stdout).get("blockdevices", []))
        if not candidates:
            return UsbStatus("unsupported_filesystem") if _has_unsupported_usb_filesystem(json.loads(result.stdout).get("blockdevices", [])) else UsbStatus("not_present")
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
                         str(candidate.mount_point) if candidate.mount_point else None)

    def one_available(self) -> UsbDevice:
        status = self.status()
        if status.state != "available":
            raise UsbExportError(status.state)
        assert status.device and status.filesystem
        return UsbDevice(status.device, status.filesystem, status.label, status.size, None)


def _usb_candidates(nodes: list[dict[str, object]], inherited_transport: str | None = None) -> list[UsbDevice]:
    candidates: list[UsbDevice] = []
    for node in nodes:
        transport = str(node.get("tran") or inherited_transport or "")
        path = node.get("path")
        filesystem = str(node.get("fstype") or "").lower()
        mount = node.get("mountpoint")
        if transport == "usb" and filesystem in USB_FILESYSTEMS and isinstance(path, str) and mount != "/":
            candidates.append(UsbDevice(path, filesystem, _optional_string(node.get("label")), node.get("size"),
                                        Path(mount) if isinstance(mount, str) and mount else None))
        children = node.get("children")
        if isinstance(children, list):
            candidates.extend(_usb_candidates(children, transport))
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


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class UsbExportService:
    """Coordinate privileged mount actions and the existing unprivileged engine."""

    def __init__(self, processed_root: Path, locator: UsbLocator, helper: Callable[[str], None],
                 exporter: Callable[..., ExportResult] = export_parquet, sync: Callable[[], None] = os.sync,
                 lock_path: Path = Path("/run/omk-export-usb.lock")) -> None:
        self._processed_root = processed_root
        self._locator = locator
        self._helper = helper
        self._exporter = exporter
        self._sync = sync
        self._lock_path = lock_path

    def export(self, from_date: date, to_date: date, datasets: Iterable[str] | None = None) -> ExportResult:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UsbExportError("busy") from error
            return self._export_locked(from_date, to_date, datasets)

    def _export_locked(self, from_date: date, to_date: date, datasets: Iterable[str] | None) -> ExportResult:
        self._locator.one_available()  # Also prevents operating on an existing desktop mount.
        mounted = False
        try:
            self._helper("mount")
            mounted = True
            status = self._locator.status()
            if status.state != "mounted" or status.mount_point != str(MOUNT_POINT):
                raise UsbExportError("mount_failed")
            output = MOUNT_POINT / "OMK"
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
                self._unmount_after_failure()
            raise
        try:
            self._helper("unmount")
        except Exception as error:
            raise UsbExportError("unmount_failed", str(error)) from error
        if self._locator.status().state == "mounted":
            raise UsbExportError("unmount_failed")
        return result

    def _unmount_after_failure(self) -> None:
        try:
            self._helper("unmount")
        except Exception:
            pass


def privileged_helper(helper_path: Path) -> Callable[[str], None]:
    def run(action: str) -> None:
        if action not in {"mount", "unmount"}:
            raise ValueError("invalid helper action")
        result = subprocess.run(["sudo", "-n", str(helper_path), action], check=False, capture_output=True, text=True)
        if result.returncode:
            raise UsbExportError(f"{action}_failed", result.stderr.strip() or result.stdout.strip())
    return run
