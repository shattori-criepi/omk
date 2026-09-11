from __future__ import annotations

from datetime import date
import io
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
    _identity_token,
)


def _lsblk(devices):
    def run(*args, **kwargs):
        import json
        return subprocess.CompletedProcess(args[0], 0, json.dumps({"blockdevices": devices}), "")
    return run


def _usb(filesystem="vfat", mounted=None):
    return [{"path": "/dev/sda", "type": "disk", "tran": "usb", "serial": "disk-a", "wwn": "wwn-a", "children": [
        {"path": "/dev/sda1", "type": "part", "tran": None, "fstype": filesystem, "label": "OMK", "size": 1024,
         "mountpoint": mounted, "uuid": "uuid-a", "partuuid": "partuuid-a"}
    ]}]


@pytest.mark.parametrize("filesystem", ["vfat", "exfat"])
def test_locator_finds_supported_partition_through_usb_parent(filesystem):
    status = UsbLocator(_lsblk(_usb(filesystem))).status()
    assert status.state == "available"
    assert status.device == "/dev/sda1"
    assert status.filesystem == filesystem
    assert status.identity == _identity_token("disk-a", "wwn-a", "uuid-a", "partuuid-a")


def test_locator_rejects_supported_usb_without_stable_identity():
    device = _usb()[0]
    device.pop("serial"); device.pop("wwn")
    device["children"][0].pop("uuid"); device["children"][0].pop("partuuid")
    assert UsbLocator(_lsblk([device])).status().state == "identity_unavailable"


def test_locator_marks_multiple_supported_usb_as_ambiguous_before_identity_filtering():
    without_identity = _usb()[0]
    without_identity.pop("serial"); without_identity.pop("wwn")
    without_identity["children"][0].pop("uuid"); without_identity["children"][0].pop("partuuid")
    assert UsbLocator(_lsblk(_usb() + [without_identity])).status().state == "ambiguous"
    another_without_identity = _usb()[0]
    another_without_identity["path"] = "/dev/sdb"
    another_without_identity["children"][0]["path"] = "/dev/sdb1"
    for device in (without_identity, another_without_identity):
        device.pop("serial", None); device.pop("wwn", None)
        device["children"][0].pop("uuid", None); device["children"][0].pop("partuuid", None)
    assert UsbLocator(_lsblk([without_identity, another_without_identity])).status().state == "ambiguous"


def test_identity_survives_device_path_reenumeration():
    first = UsbLocator(_lsblk(_usb())).status()
    reenumerated = _usb()
    reenumerated[0]["path"] = "/dev/sdb"
    reenumerated[0]["children"][0]["path"] = "/dev/sdb1"
    second = UsbLocator(_lsblk(reenumerated)).status()
    assert first.device == "/dev/sda1" and second.device == "/dev/sdb1"
    assert first.identity == second.identity


def test_locator_handles_absent_ambiguous_and_non_usb():
    assert UsbLocator(_lsblk([])).status().state == "not_present"
    assert UsbLocator(_lsblk(_usb() + [{"path": "/dev/sdb", "tran": "usb", "serial": "disk-b", "fstype": "exfat", "uuid": "uuid-b", "mountpoint": None}])).status().state == "ambiguous"
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
        return UsbDevice(status.device, status.filesystem, status.label, status.size, Path(status.mount_point) if status.mount_point else None, status.identity or "")


def test_export_mounts_writes_fixed_directory_syncs_and_unmounts(tmp_path, monkeypatch):
    locator = MutableLocator(); actions = []; synced = []
    monkeypatch.setattr("data_exporter.usb.MOUNT_POINT", tmp_path / "mount")
    def helper(action, *_):
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
    def helper(action, *_):
        actions.append(action); locator.mounted = action == "mount"
        if action == "mount": (tmp_path / "mount").mkdir(exist_ok=True)
    service = UsbExportService(tmp_path, locator, helper, lambda *args: (_ for _ in ()).throw(RuntimeError("export")), lock_path=tmp_path / "lock")
    with pytest.raises(RuntimeError): service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert actions == ["mount", "unmount"]
    locator = MutableLocator()
    def bad_unmount(action, *_):
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
    def helper(action, *_):
        actions.append(action)
        if action == "unmount": locator.mounted = False
    def exporter(root, output, *_):
        assert output == mount / "OMK"
        return ExportResult(output / "done.zip", (), {})
    UsbExportService(tmp_path, locator, helper, exporter, lock_path=tmp_path / "lock").export(date(2026, 8, 1), date(2026, 8, 1))
    assert actions == ["unmount"]


def test_export_rejects_changed_usb_before_writing_or_unmounting_replacement(tmp_path):
    mount = tmp_path / "media"; mount.mkdir()
    identity_a, identity_b = "a" * 64, "b" * 64

    class SwappedAutomountLocator:
        def __init__(self): self.calls = 0
        def one_exportable(self):
            self.calls += 1
            identity = identity_a if self.calls == 1 else identity_b
            return UsbDevice("/dev/sda1" if identity == identity_a else "/dev/sdb1", "vfat", "OMK", 1, mount, identity)
        def status(self): return UsbStatus("mounted", mount_point=str(mount), identity=identity_b)

    helper_calls = []
    exporter_calls = []
    service = UsbExportService(tmp_path, SwappedAutomountLocator(), lambda action, token: helper_calls.append((action, token)),
                               lambda *args: exporter_calls.append(args), lock_path=tmp_path / "lock")
    with pytest.raises(UsbExportError, match="usb_changed") as error:
        service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert error.value.code == "usb_changed"
    assert exporter_calls == []
    assert helper_calls == [("unmount", identity_a)]


def test_unmount_identity_mismatch_is_not_relabelled_or_sent_to_helper(tmp_path):
    mount = tmp_path / "media"; mount.mkdir()
    identity_a, identity_b = "a" * 64, "b" * 64

    class ChangesBeforeUnmount:
        def __init__(self): self.calls = 0
        def one_exportable(self):
            self.calls += 1
            identity = identity_b if self.calls == 3 else identity_a
            return UsbDevice("/dev/sdb1" if identity == identity_b else "/dev/sda1", "vfat", "OMK", 1, mount, identity)
        def status(self): return UsbStatus("mounted", mount_point=str(mount), identity=identity_a)

    helper_calls = []
    service = UsbExportService(tmp_path, ChangesBeforeUnmount(), lambda action, token: helper_calls.append((action, token)),
                               lambda *args: ExportResult(tmp_path / "done.zip", (), {}), lock_path=tmp_path / "lock")
    with pytest.raises(UsbExportError, match="usb_changed") as error:
        service.export(date(2026, 8, 1), date(2026, 8, 1))
    assert error.value.code == "usb_changed"
    assert helper_calls == []


def test_export_rejects_changed_usb_before_helper_mount(tmp_path, monkeypatch):
    monkeypatch.setattr("data_exporter.usb.MOUNT_POINT", tmp_path / "mount")
    locator = MutableLocator()
    calls = []
    def helper(action, token):
        calls.append((action, token))
        raise UsbExportError("usb_changed")
    service = UsbExportService(tmp_path, locator, helper, lock_path=tmp_path / "lock")
    with pytest.raises(UsbExportError, match="usb_changed"):
        service.export(date(2026, 8, 1), date(2026, 8, 1), expected_identity=locator.one_exportable().identity)
    assert calls == [("mount", locator.one_exportable().identity)]


class VerificationLocator:
    def __init__(self, mount: Path, states: list[str]) -> None:
        self.mount = mount
        self.states = iter(states)

    def one_exportable(self) -> UsbDevice:
        return UsbDevice("/dev/sda1", "vfat", "OMK", 1024, self.mount, "a" * 64)

    def status(self) -> UsbStatus:
        state = next(self.states)
        return UsbStatus(state, "/dev/sda1", "vfat", mount_state="mounted" if state == "mounted" else "unmounted", identity="a" * 64)


def test_unmount_verification_retries_until_usb_is_unmounted(tmp_path):
    mount = tmp_path / "media"; mount.mkdir()
    sleeps = []
    service = UsbExportService(
        tmp_path,
        VerificationLocator(mount, ["mounted", "available"]),
        lambda action, identity: action == "unmount",
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
        lambda action, identity: action == "unmount",
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
        service = UsbExportService(tmp_path, MutableLocator(), lambda *_: None, lock_path=lock)
        with pytest.raises(UsbExportError, match="busy"):
            service.export(date(2026, 8, 1), date(2026, 8, 1))


def test_default_lock_is_under_user_writable_data_directory(tmp_path):
    processed = tmp_path / "data" / "processed"
    service = UsbExportService(processed, MutableLocator(), lambda _: None)
    assert service._lock_path == tmp_path / "data" / ".omk-export-usb.lock"


def test_helper_requests_tree_columns_and_inherits_usb_transport(monkeypatch):
    helper = Path(__file__).parents[3] / "scripts" / "omk-export-usb-helper"
    namespace = runpy.run_path(str(helper))
    monkeypatch.setitem(namespace["host_run"].__globals__, "NSENTER", "/usr/bin/nsenter")
    calls = []
    def run(arguments, **kwargs):
        import json
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"blockdevices": _usb()}), "")
    monkeypatch.setitem(namespace["subprocess"].__dict__, "run", run)
    found = namespace["candidate"](_identity_token("disk-a", "wwn-a", "uuid-a", "partuuid-a"))
    assert found["path"] == "/dev/sda1"
    assert calls == [["/usr/bin/nsenter", "--mount=/proc/1/ns/mnt", "--", "lsblk", "--json", "--bytes", "-o", "NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT,SERIAL,WWN,UUID,PARTUUID"]]


@pytest.mark.parametrize(("action", "mountpoint", "expected_command"), [
    ("mount", None, "systemd-mount"),
    ("unmount", "/media/omkdev/UUID", "systemd-umount"),
])
def test_helper_runs_mount_actions_in_host_mount_namespace(monkeypatch, action, mountpoint, expected_command):
    helper = Path(__file__).parents[3] / "scripts" / "omk-export-usb-helper"
    namespace = runpy.run_path(str(helper))
    monkeypatch.setitem(namespace["host_run"].__globals__, "NSENTER", "/usr/bin/nsenter")
    calls = []
    def run(arguments, **kwargs):
        import json
        calls.append((arguments, kwargs))
        if arguments[-1] == "NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT,SERIAL,WWN,UUID,PARTUUID":
            return subprocess.CompletedProcess(arguments, 0, json.dumps({"blockdevices": _usb(mounted=mountpoint)}), "")
        return subprocess.CompletedProcess(arguments, 0, "", "")
    monkeypatch.setitem(namespace["subprocess"].__dict__, "run", run)
    monkeypatch.setattr(namespace["sys"], "argv", ["helper", action])
    monkeypatch.setattr(namespace["sys"], "stdin", io.StringIO(_identity_token("disk-a", "wwn-a", "uuid-a", "partuuid-a") + "\n"))
    namespace["main"]()
    assert calls[0][0][:3] == ["/usr/bin/nsenter", "--mount=/proc/1/ns/mnt", "--"]
    assert calls[1][0][:3] == ["/usr/bin/nsenter", "--mount=/proc/1/ns/mnt", "--"]
    assert calls[1][0][3] == expected_command


def test_helper_does_not_mount_or_unmount_when_identity_changed(monkeypatch):
    helper = Path(__file__).parents[3] / "scripts" / "omk-export-usb-helper"
    namespace = runpy.run_path(str(helper))
    monkeypatch.setitem(namespace["host_run"].__globals__, "NSENTER", "/usr/bin/nsenter")
    calls = []
    def run(arguments, **kwargs):
        import json
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"blockdevices": _usb()}), "")
    monkeypatch.setitem(namespace["subprocess"].__dict__, "run", run)
    monkeypatch.setattr(namespace["sys"], "argv", ["helper", "mount"])
    monkeypatch.setattr(namespace["sys"], "stdin", io.StringIO("b" * 64 + "\n"))
    with pytest.raises(RuntimeError, match="usb_changed"):
        namespace["main"]()
    assert len(calls) == 1 and "systemd-mount" not in calls[0]


@pytest.mark.parametrize("token", ["", "invalid", "a" * 63])
def test_helper_rejects_missing_or_invalid_stdin_token_without_host_operations(monkeypatch, token):
    helper = Path(__file__).parents[3] / "scripts" / "omk-export-usb-helper"
    namespace = runpy.run_path(str(helper))
    calls = []
    monkeypatch.setitem(namespace["subprocess"].__dict__, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(namespace["sys"], "argv", ["helper", "mount"])
    monkeypatch.setattr(namespace["sys"], "stdin", io.StringIO(token))
    with pytest.raises(SystemExit, match="valid identity token required"):
        namespace["main"]()
    assert calls == []


def test_privileged_helper_keeps_identity_out_of_sudo_argv(monkeypatch, tmp_path):
    from data_exporter.usb import privileged_helper
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, "", "")
    monkeypatch.setattr("data_exporter.usb.subprocess.run", run)
    identity = "a" * 64
    privileged_helper(tmp_path / "helper")("mount", identity)
    assert calls[0][0] == ["sudo", "-n", str(tmp_path / "helper"), "mount"]
    assert identity not in calls[0][0] and calls[0][1]["input"] == f"{identity}\n"
