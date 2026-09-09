"""Administrator trust storage and its unprivileged recovery boundary."""

import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from broute_meter import usb_recovery as usb


@pytest.fixture
def identity(tmp_path, monkeypatch):
    path = tmp_path / "etc/omk/broute-usb-recovery.conf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"TEST_ADAPTER_A\n")
    path.chmod(0o644)
    monkeypatch.setattr(usb, "TRUSTED_IDENTITY_PATH", path)
    original = Path.lstat

    def root_stat(item):
        info = original(item)
        return SimpleNamespace(st_uid=0, st_gid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)

    # Only ownership is mocked; real symlink, hardlink, modes and bytes are tested.
    monkeypatch.setattr(Path, "lstat", root_stat)
    return path


def device():
    return usb.UsbDevice(Path("/dev/ttyUSB9"), Path("/sys/devices/1-1.2"),
                         "0403", "6015", "TEST_ADAPTER_A")


def test_read_root_owned_identity(identity):
    assert usb.read_trusted_usb_serial() == "TEST_ADAPTER_A"


@pytest.mark.parametrize("content", [b"", b"TEST_ADAPTER_A", b"TEST_ADAPTER_A\n\n",
    b"TEST_ADAPTER_A\r\n", b"SERIAL=A\n", b"../A\n", b"$(id)\n", b"\xff\n", b"A" * 65 + b"\n"])
def test_bad_identity_format(identity, content):
    identity.write_bytes(content)
    with pytest.raises(usb.UsbRecoveryError):
        usb.read_trusted_usb_serial()


@pytest.mark.parametrize("problem", ["missing", "symlink", "hardlink", "file_mode",
                                    "directory_mode", "parent_mode", "directory_symlink"])
def test_unsafe_identity(identity, problem):
    if problem == "missing":
        identity.unlink()
    elif problem == "symlink":
        target = identity.with_suffix(".other")
        identity.rename(target)
        identity.symlink_to(target)
    elif problem == "hardlink":
        identity.with_suffix(".other").hardlink_to(identity)
    elif problem == "file_mode":
        identity.chmod(0o666)
    elif problem == "directory_mode":
        identity.parent.chmod(0o777)
    elif problem == "parent_mode":
        identity.parent.parent.chmod(0o777)
    else:
        target = identity.parent.with_name("other")
        identity.parent.rename(target)
        identity.parent.symlink_to(target)
    with pytest.raises(usb.UsbRecoveryError):
        usb.read_trusted_usb_serial()


@pytest.mark.parametrize("uid,gid", [(1000, 0), (0, 1000)])
def test_owner_group_guard(monkeypatch, uid, gid):
    monkeypatch.setattr(Path, "lstat", lambda _: SimpleNamespace(
        st_uid=uid, st_gid=gid, st_mode=stat.S_IFREG | 0o644, st_nlink=1))
    with pytest.raises(usb.UsbRecoveryError):
        usb._check_root_owned(Path("/fake"))


def test_missing_trust_never_calls_helper(identity, monkeypatch):
    identity.unlink()
    run = Mock()
    monkeypatch.setattr(usb.subprocess, "run", run)
    with pytest.raises(usb.UsbRecoveryError, match="disabled"):
        usb.RsWsuhaPUsbResetter(Path("/dev/ttyUSB9"), Path("/root/helper")).reset()
    run.assert_not_called()


def test_other_ft230x_is_not_trusted(identity, monkeypatch):
    monkeypatch.setattr(usb, "resolve_rs_wsuha_p_usb", lambda _: replace(device(), serial="TEST_OTHER"))
    run = Mock()
    monkeypatch.setattr(usb.subprocess, "run", run)
    with pytest.raises(usb.UsbRecoveryError, match="does not match"):
        usb.RsWsuhaPUsbResetter(Path("/dev/ttyUSB9"), Path("/root/helper")).reset()
    run.assert_not_called()


def test_registration_requires_root(identity, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(usb.UsbRecoveryError, match="administrator"):
        usb.register_trusted_usb_adapter(device(), device())


def test_registration_requires_stable_identity(identity, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(usb.UsbRecoveryError, match="changed"):
        usb.register_trusted_usb_adapter(device(), replace(device(), serial="TEST_OTHER"))


def test_registration_exclusive_and_idempotent(identity, monkeypatch):
    identity.unlink()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda *_: None)
    usb.register_trusted_usb_adapter(device(), device())
    assert usb.read_trusted_usb_serial() == "TEST_ADAPTER_A"
    before = identity.stat().st_ino
    usb.register_trusted_usb_adapter(device(), device())
    assert identity.stat().st_ino == before
    other = replace(device(), serial="TEST_OTHER")
    with pytest.raises(usb.UsbRecoveryError, match="already trusted"):
        usb.register_trusted_usb_adapter(other, other)
    assert identity.read_text() == "TEST_ADAPTER_A\n"
    assert not list(identity.parent.glob(".broute-usb-*"))


def test_registration_creates_protected_directory(identity, monkeypatch):
    identity.unlink()
    identity.parent.rmdir()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda *_: None)
    chown = Mock()
    monkeypatch.setattr(os, "chown", chown)
    usb.register_trusted_usb_adapter(device(), device())
    assert usb.read_trusted_usb_serial() == "TEST_ADAPTER_A"
    assert stat.S_IMODE(identity.parent.stat().st_mode) == 0o755
    chown.assert_called_once_with(identity.parent, 0, 0)


def test_registration_refuses_unsafe_directory(identity, monkeypatch):
    identity.unlink()
    identity.parent.chmod(0o777)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(usb.UsbRecoveryError):
        usb.register_trusted_usb_adapter(device(), device())
    assert not identity.exists()


def test_concurrent_registration_is_not_overwritten(identity, monkeypatch):
    identity.unlink()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda *_: None)
    original_link = os.link

    def race(source, target):
        target.write_text("TEST_OTHER\n")
        original_link(source, target)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(usb.UsbRecoveryError):
        usb.register_trusted_usb_adapter(device(), device())
    assert identity.read_text() == "TEST_OTHER\n"
    assert not list(identity.parent.glob(".broute-usb-*"))
