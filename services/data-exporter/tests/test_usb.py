from __future__ import annotations

from datetime import date
from pathlib import Path
import runpy
import subprocess

import pytest

from data_exporter.engine import ExportResult
from data_exporter.usb import (
    MOUNT_POINT,
    UNMOUNT_VERIFICATION_ATTEMPTS,
    UNMOUNT_VERIFICATION_INTERVAL_SECONDS,
    UsbDevice,
    UsbExportError,
    UsbExportService,
    UsbLocator,
    UsbStatus,
)


def _lsblk(devices):
    def run(*args, **kwargs):
        import json
        return subprocess.CompletedProcess(args[0], 0, json.dumps({"blockdevices": devices}), "")
    return run


def _usb(filesystem="vfat", mounted=None):
    return [{"path": "/dev/sda", "type": "disk", "tran": "usb", "children": [
        {"path": "/dev/sda1", "type": "part", "tran": None, "fstype": filesystem, "label": "OMK", "size": 1024,
         "mountpoint": mounted}
    ]}]


@pytest.mark.parametrize("filesystem", ["vfat", "exfat"])
def test_locator_finds_supported_partition_through_usb_parent(filesystem):
    status = UsbLocator(_lsblk(_usb(filesystem))).status()
    assert status.state == "available"
    assert status.device == "/dev/sda1"
    assert status.filesystem == filesystem


def test_locator_handles_absent_ambiguous_and_non_usb():
    assert UsbLocator(_lsblk([])).status().state == "not_present"
    assert UsbLocator(_lsblk(_usb() + [{"path": "/dev/sdb", "tran": "usb", "fstype": "exfat", "mountpoint": None}])).status().state == "ambiguous"
    assert UsbLocator(_lsblk([{"path": "/dev/nvme0n1p1", "tran": "nvme", "fstype": "vfat", "mountpoint": None}])).status().state == "not_present"
    assert UsbLocator(_lsblk(_usb("ntfs"))).status().state == "unsupported_filesystem"


class MutableLocator:
    def __init__(self, mount_path=None): self.mounted = mount_path is not None; self.mount_path = mount_path
    def status(self):
        import data_exporter.usb as usb
        path = self.mount_path if self.mounted and self.mount_path else (str(usb.MOUNT_POINT) if self.mounted else None)
        return UsbLocator(_lsblk(_usb(mounted=path))).status()
    def one_exportable(self):
        status = self.status()
        if status.state not in {"available", "mounted"}: raise UsbExportError(status.state)
        from data_exporter.usb import UsbDevice
        return UsbDevice(status.device, status.filesystem, status.label, status.size, Path(status.mount_point) if status.mount_point else None)


def test_export_mounts_writes_fixed_directory_syncs_and_unmounts(tmp_path, monkeypatch):
    locator = MutableLocator(); actions = []; synced = []
    monkeypatch.setattr("data_exporter.usb.MOUNT_POINT", tmp_path / "mount")
    def helper(action):
        actions.append(action); locator.mounted = action == "mount"
        if action == "mount": (tmp_path / "mount").mkdir(exist_ok=True)
    def exporter(root, output, *_):
        assert output == tmp_path / "mount" / "OMK"; return ExportResult(output / "done.zip", ("sen66",), {"sen66": 1})
    service = UsbExportService(tmp_path, locator, helper, exporter, lambda: synced.append(True), tmp_path / "lock")
    service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert actions == ["mount", "unmount"] and synced == [True]


def test_export_failure_unmounts_and_unmount_failure_is_not_success(tmp_path, monkeypatch):
    locator = MutableLocator(); monkeypatch.setattr("data_exporter.usb.MOUNT_POINT", tmp_path / "mount")
    actions = []
    def helper(action):
        actions.append(action); locator.mounted = action == "mount"
        if action == "mount": (tmp_path / "mount").mkdir(exist_ok=True)
    service = UsbExportService(tmp_path, locator, helper, lambda *args: (_ for _ in ()).throw(RuntimeError("export")), lock_path=tmp_path / "lock")
    with pytest.raises(RuntimeError): service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert actions == ["mount", "unmount"]
    locator = MutableLocator()
    def bad_unmount(action):
        if action == "mount":
            locator.mounted = True
            (tmp_path / "mount").mkdir(exist_ok=True)
        else: raise RuntimeError("busy")
    service = UsbExportService(tmp_path, locator, bad_unmount, lambda *args: ExportResult(tmp_path / "x", (), {}), lock_path=tmp_path / "lock2")
    with pytest.raises(UsbExportError, match="busy") as error: service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert error.value.code == "unmount_failed"


def test_export_uses_existing_automount_and_unmounts_it(tmp_path, monkeypatch):
    mount = tmp_path / "media" / "UUID"; mount.mkdir(parents=True)
    locator = MutableLocator(str(mount)); actions = []
    monkeypatch.setattr("data_exporter.usb.os.access", lambda path, mode: True)
    def helper(action):
        actions.append(action)
        if action == "unmount": locator.mounted = False
    def exporter(root, output, *_):
        assert output == mount / "OMK"
        return ExportResult(output / "done.zip", (), {})
    UsbExportService(tmp_path, locator, helper, exporter, lock_path=tmp_path / "lock").export(date(2026, 8, 1), date(2026, 8, 1))
    assert actions == ["unmount"]


class VerificationLocator:
    def __init__(self, mount: Path, states: list[str]) -> None:
        self.mount = mount
        self.states = iter(states)

    def one_exportable(self) -> UsbDevice:
        return UsbDevice("/dev/sda1", "vfat", "OMK", 1024, self.mount)

    def status(self) -> UsbStatus:
        state = next(self.states)
        return UsbStatus(state, "/dev/sda1", "vfat", mount_state="mounted" if state == "mounted" else "unmounted")


def test_unmount_verification_retries_until_usb_is_unmounted(tmp_path):
    mount = tmp_path / "media"; mount.mkdir()
    sleeps = []
    service = UsbExportService(
        tmp_path,
        VerificationLocator(mount, ["mounted", "available"]),
        lambda action: action == "unmount",
        lambda *args: ExportResult(tmp_path / "x.zip", (), {}),
        lock_path=tmp_path / "lock",
        sleep=sleeps.append,
    )
    service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert sleeps == [UNMOUNT_VERIFICATION_INTERVAL_SECONDS]


def test_unmount_verification_fails_when_usb_remains_mounted(tmp_path):
    mount = tmp_path / "media"; mount.mkdir()
    sleeps = []
    service = UsbExportService(
        tmp_path,
        VerificationLocator(mount, ["mounted"] * UNMOUNT_VERIFICATION_ATTEMPTS),
        lambda action: action == "unmount",
        lambda *args: ExportResult(tmp_path / "x.zip", (), {}),
        lock_path=tmp_path / "lock",
        sleep=sleeps.append,
    )
    with pytest.raises(UsbExportError, match="unmount_failed") as error:
        service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert error.value.code == "unmount_failed"
    assert sleeps == [UNMOUNT_VERIFICATION_INTERVAL_SECONDS] * (UNMOUNT_VERIFICATION_ATTEMPTS - 1)


def test_lock_prevents_second_export(tmp_path):
    import fcntl
    lock = tmp_path / "lock"; lock.touch()
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        service = UsbExportService(tmp_path, MutableLocator(), lambda _: None, lock_path=lock)
        with pytest.raises(UsbExportError, match="busy"):
            service.export(date(2026, 8, 1), date(2026, 8, 1))


def test_default_lock_is_under_user_writable_data_directory(tmp_path):
    processed = tmp_path / "data" / "processed"
    service = UsbExportService(processed, MutableLocator(), lambda _: None)
    assert service._lock_path == tmp_path / "data" / ".omk-export-usb.lock"


def test_helper_requests_tree_columns_and_inherits_usb_transport(monkeypatch):
    helper = Path(__file__).parents[3] / "scripts" / "omk-export-usb-helper"
    namespace = runpy.run_path(str(helper))
    calls = []
    def run(arguments, **kwargs):
        import json
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"blockdevices": _usb()}), "")
    monkeypatch.setitem(namespace["subprocess"].__dict__, "run", run)
    found = namespace["candidate"]()
    assert found["path"] == "/dev/sda1"
    assert calls == [["lsblk", "--json", "--bytes", "-o", "NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT"]]
