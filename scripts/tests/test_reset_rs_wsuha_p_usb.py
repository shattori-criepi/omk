"""Run the real shell validation against temporary sysfs/driver files, never USB."""

import subprocess
from pathlib import Path

import pytest


HELPER = Path(__file__).resolve().parents[1] / "reset-rs-wsuha-p-usb.sh"


def adapter(root, serial="TEST_ADAPTER_A", usb="1-1.2", tty="ttyUSB9"):
    device = root / "sys/devices" / usb
    interface = device / f"{usb}:1.0" / tty
    interface.mkdir(parents=True)
    for field, value in {"idVendor": "0403", "idProduct": "6015",
                         "product": "FT230X Basic UART", "serial": serial}.items():
        (device / field).write_text(value + "\n")
    entry = root / "sys/class/tty" / tty
    entry.mkdir(parents=True)
    (entry / "device").symlink_to(interface)
    (entry / "dev").write_text("1:3\n")
    bus = root / "sys/bus/usb/devices"
    bus.mkdir(parents=True, exist_ok=True)
    (bus / usb).symlink_to(device)
    (root / "dev").mkdir(exist_ok=True)
    (root / "dev" / tty).symlink_to("/dev/null")
    return device


@pytest.fixture
def fake(tmp_path):
    driver = tmp_path / "sys/bus/usb/drivers/usb"
    driver.mkdir(parents=True)
    for action in ("unbind", "bind"):
        (driver / action).touch()
    adapter(tmp_path)
    identity = tmp_path / "etc/omk/broute-usb-recovery.conf"
    identity.parent.mkdir(parents=True)
    identity.write_text("TEST_ADAPTER_A\n")
    identity.chmod(0o644)
    (tmp_path / "run").mkdir()
    return tmp_path


def run(root, args=(), extra="", helper=HELPER):
    # Overrides exist only in this sourced test process, not the installed helper.
    return subprocess.run(
        ["bash", "-c", '''
source "$1"
main() { reset_main "$@"; }
if [[ "$3" != "$1" ]]; then source "$3"; fi
load_usb_guard() { :; }
STATE_DIR="$2/run/omk-broute-usb"
SYS_ROOT="$2/sys"
DEV_ROOT="$2/dev"
IDENTITY_FILE="$2/etc/omk/broute-usb-recovery.conf"
# Fake only ownership; real file type, link count and mode remain under test.
stat() {
    case "$2" in
        '%u:%g:%a') printf '%s:%s:%s\n' "${TEST_UID:-0}" "${TEST_GID:-0}" "$(command stat -c '%a' "$3")" ;;
        '%u:%g:%a:%h') printf '%s:%s:%s\n' "${TEST_UID:-0}" "${TEST_GID:-0}" "$(command stat -c '%a:%h' "$3")" ;;
        *) command stat "$@" ;;
    esac
}
is_root() { return 0; }
pause() { :; }
shift 3
''' + extra + '\nmain "$@"', "test", str(HELPER), str(root), str(helper), *args],
        capture_output=True, text=True, timeout=5,
    )


def untouched(root):
    for action in ("unbind", "bind"):
        assert (root / "sys/bus/usb/drivers/usb" / action).read_text() == ""


@pytest.mark.parametrize("serial", ["TEST_ADAPTER_A", "TEST_ADAPTER_B"])
def test_dynamic_serial_resets_only_matching_parent(fake, serial):
    (fake / "sys/devices/1-1.2/serial").write_text(serial)
    (fake / "etc/omk/broute-usb-recovery.conf").write_text(serial + "\n")
    adapter(fake, serial="TEST_OTHER", usb="1-1.3", tty="ttyUSB8")
    result = run(fake)
    assert result.returncode == 0, result.stderr
    for action in ("unbind", "bind"):
        assert (fake / "sys/bus/usb/drivers/usb" / action).read_text() == "1-1.2"
    assert serial not in result.stdout


@pytest.mark.parametrize("args", [("TEST_ADAPTER_A",), ("A", "B"), ("/dev/sda",), ("../A",),
    ("A B",), ("A\nB",), ("A;id",), ("$(id)",), ("-A",), ("A" * 65,), ("",)])
def test_malformed_arguments(fake, args):
    assert run(fake, args).returncode == 2
    untouched(fake)


@pytest.mark.parametrize("field,value", [("serial", "TEST_OTHER"),
    ("idVendor", "1234"), ("idProduct", "9999"), ("serial", "")])
def test_identity_mismatch(fake, field, value):
    (fake / "sys/devices/1-1.2" / field).write_text(value)
    assert run(fake).returncode != 0
    untouched(fake)


def test_ambiguous_serial(fake):
    adapter(fake, usb="1-1.3", tty="ttyUSB8")
    assert run(fake).returncode != 0
    untouched(fake)


@pytest.mark.parametrize("path", ["sys/class/tty/ttyUSB9/device",
    "sys/bus/usb/devices/1-1.2", "dev/ttyUSB9"])
def test_missing_correspondence(fake, path):
    (fake / path).unlink()
    assert run(fake).returncode != 0
    untouched(fake)


def test_wrong_character_device(fake):
    (fake / "sys/class/tty/ttyUSB9/dev").write_text("1:5\n")
    assert run(fake).returncode != 0
    untouched(fake)


def test_sysfs_escape(fake):
    link = fake / "sys/class/tty/ttyUSB9/device"
    link.unlink()
    link.symlink_to(fake)
    assert run(fake).returncode != 0
    untouched(fake)


def test_nonroot(fake):
    assert run(fake, extra="is_root() { return 1; }").returncode != 0
    untouched(fake)


@pytest.mark.parametrize("content", ["", "TEST_ADAPTER_A", "TEST_ADAPTER_A\n\n",
    "SERIAL=TEST_ADAPTER_A\n", "TEST_ADAPTER_A\r\n", "../A\n", "$(id)\n", "A" * 65 + "\n"])
def test_malformed_identity_file(fake, content):
    (fake / "etc/omk/broute-usb-recovery.conf").write_text(content)
    assert run(fake).returncode != 0
    untouched(fake)


@pytest.mark.parametrize("problem", ["missing", "symlink", "hardlink", "owner", "group",
    "file_mode", "directory_mode", "parent_mode", "directory_symlink"])
def test_unsafe_identity_file(fake, problem):
    path = fake / "etc/omk/broute-usb-recovery.conf"
    extra = ""
    if problem == "missing":
        path.unlink()
    elif problem == "symlink":
        target = path.with_suffix(".other")
        path.rename(target)
        path.symlink_to(target)
    elif problem == "hardlink":
        path.with_suffix(".other").hardlink_to(path)
    elif problem == "owner":
        extra = "TEST_UID=1000"
    elif problem == "group":
        extra = "TEST_GID=1000"
    elif problem == "file_mode":
        path.chmod(0o666)
    elif problem == "directory_mode":
        path.parent.chmod(0o777)
    elif problem == "parent_mode":
        path.parent.parent.chmod(0o777)
    else:
        target = path.parent.with_name("other")
        path.parent.rename(target)
        path.parent.symlink_to(target)
    assert run(fake, extra=extra).returncode != 0
    untouched(fake)


def test_identity_changed_before_bind(fake):
    result = run(fake, extra='pause() { printf TEST_OTHER > "$SYS_ROOT/devices/1-1.2/serial"; }')
    assert result.returncode != 0
    assert (fake / "sys/bus/usb/drivers/usb/bind").read_text() == ""


def test_delayed_matching_reappearance(fake):
    result = run(fake, extra='''
pause() {
    if [[ "$1" == 2 ]]; then
        unlink "$DEV_ROOT/ttyUSB9"
    else
        ln -s /dev/null "$DEV_ROOT/ttyUSB9"
    fi
}
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("replacement", [False, True])
def test_bounded_wait_rejects_missing_or_changed_identity(fake, replacement):
    extra = '''
pause() {
    if [[ "$1" == 2 ]]; then unlink "$DEV_ROOT/ttyUSB9"; fi
    printf x >> "$SYS_ROOT/waits"
'''
    if replacement:
        extra += '''
    if [[ "$1" == 1 && ! -e "$DEV_ROOT/ttyUSB9" ]]; then
        printf TEST_OTHER > "$SYS_ROOT/devices/1-1.2/serial"
        ln -s /dev/null "$DEV_ROOT/ttyUSB9"
    fi
'''
    result = run(fake, extra=extra + "\n}")
    assert result.returncode != 0
    assert "did not reappear" in result.stderr
    assert (fake / "sys/waits").read_text() == "x" * 16
