"""Execute the sudo boundary with fake USB trees and record every power command."""
import shutil

import pytest

from test_reset_rs_wsuha_p_usb import HELPER, adapter, fake, run  # noqa: F401

VBUS = HELPER.with_name("cycle-gateway-usb-vbus.sh")
ENV = '''
UHUBCTL=/bin/true
read_model() { echo 'Raspberry Pi 4 Model B'; }
uptime_seconds() { echo "${TEST_NOW:-1000}"; }
run_uhubctl() {
  if [[ "$*" == '-l 1-1' ]]; then
    echo 'Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]'
  else
    printf '%s\\n' "$*" >> "$SYS_ROOT/power-writes"
  fi
}
'''


@pytest.fixture
def gateway(fake):
    old = fake / "sys/devices/1-1.2"
    hub = fake / "sys/devices/1-1"
    hub.mkdir()
    (hub / "idVendor").write_text("2109\n")
    (hub / "idProduct").write_text("3431\n")
    (fake / "sys/bus/usb/devices/1-1").symlink_to(hub)
    old.rename(hub / old.name)
    for link, target in [
        (fake / "sys/bus/usb/devices/1-1.2", hub / old.name),
        (fake / "sys/class/tty/ttyUSB9/device", hub / old.name / "1-1.2:1.0/ttyUSB9"),
    ]:
        link.unlink()
        link.symlink_to(target)
    return fake


def armed(gateway):
    result = run(gateway, extra=ENV)
    assert result.returncode == 0, result.stderr
    assert (gateway / "run/omk-broute-usb/pending").exists()


def cycle(root, extra="", args=()):
    return run(root, args=args, extra=ENV + extra, helper=VBUS)


def no_power(root, result):
    assert result.returncode != 0, result.stdout + result.stderr
    assert not (root / "sys/power-writes").exists()


def test_direct_call_requires_logical_reset_ticket(gateway):
    no_power(gateway, cycle(gateway))


def test_authorized_cycle_consumes_ticket_and_records_exact_hub(gateway):
    armed(gateway)
    result = cycle(gateway)
    assert result.returncode == 0, result.stderr
    assert (gateway / "sys/power-writes").read_text().splitlines() == [
        "-l 1-1 -a cycle -d 5", "-l 1-1 -a on"]
    (gateway / "sys/power-writes").unlink()
    no_power(gateway, cycle(gateway))
    armed(gateway)
    no_power(gateway, cycle(gateway, "TEST_NOW=1100"))


@pytest.mark.parametrize("problem", ["missing", "symlink", "hardlink", "owner", "writable", "malformed"])
def test_identity_cannot_be_bypassed_by_direct_call(gateway, problem):
    armed(gateway)
    identity = gateway / "etc/omk/broute-usb-recovery.conf"
    extra = ""
    if problem == "missing":
        identity.unlink()
    elif problem == "symlink":
        other = identity.with_suffix(".other")
        identity.rename(other)
        identity.symlink_to(other)
    elif problem == "hardlink":
        identity.with_suffix(".other").hardlink_to(identity)
    elif problem == "owner":
        extra = "TEST_UID=1000"
    elif problem == "writable":
        identity.chmod(0o666)
    else:
        identity.write_text("SERIAL=TEST_ADAPTER_A\n")
    no_power(gateway, cycle(gateway, extra))


@pytest.mark.parametrize("extra", [
    "read_model() { echo 'Raspberry Pi 5'; }",
    "run_uhubctl() { echo 'wrong hub'; }",
    "TEST_NOW=1601", "TEST_NOW=999",
])
def test_platform_topology_and_ticket_expiry(gateway, extra):
    armed(gateway)
    no_power(gateway, cycle(gateway, extra))


@pytest.mark.parametrize("args", [("/dev/ttyUSB0",), ("TEST_OTHER",), ("1-1",), ("",)])
def test_no_caller_selected_target(gateway, args):
    armed(gateway)
    no_power(gateway, cycle(gateway, args=args))


def test_other_serial_at_old_slot_refused(gateway):
    armed(gateway)
    (gateway / "sys/devices/1-1/1-1.2/serial").write_text("TEST_OTHER\n")
    no_power(gateway, cycle(gateway))


def test_duplicate_even_without_tty_refused(gateway):
    armed(gateway)
    adapter(gateway, usb="1-1.3", tty="ttyUSB8")
    shutil.rmtree(gateway / "sys/class/tty/ttyUSB8")
    no_power(gateway, cycle(gateway))


def test_complete_disappearance_requires_fresh_ticket(gateway):
    armed(gateway)
    (gateway / "sys/bus/usb/devices/1-1.2").unlink()
    shutil.rmtree(gateway / "sys/class/tty/ttyUSB9")
    shutil.rmtree(gateway / "sys/devices/1-1/1-1.2")
    result = cycle(gateway)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("problem", ["symlink", "hardlink", "mode", "malformed", "directory", "expired"])
def test_unsafe_runtime_ticket(gateway, problem):
    armed(gateway)
    path = gateway / "run/omk-broute-usb/pending"
    if problem == "symlink":
        other = path.with_suffix(".other")
        path.rename(other)
        path.symlink_to(other)
    elif problem == "hardlink":
        path.with_suffix(".other").hardlink_to(path)
    elif problem == "mode":
        path.chmod(0o666)
    elif problem == "directory":
        path.parent.chmod(0o777)
    elif problem == "expired":
        path.write_text("TEST_ADAPTER_A\n1-1.2\n0\n")
    else:
        path.write_text("$(id)\n")
    no_power(gateway, cycle(gateway))


def test_wrong_sysfs_hub_refused(gateway):
    armed(gateway)
    (gateway / "sys/devices/1-1/idProduct").write_text("9999")
    no_power(gateway, cycle(gateway))


def test_unbound_parent_with_fresh_ticket_can_recover(gateway):
    armed(gateway)
    shutil.rmtree(gateway / "sys/class/tty/ttyUSB9")
    (gateway / "dev/ttyUSB9").unlink()
    result = cycle(gateway)
    assert result.returncode == 0, result.stderr


def test_failed_power_attempt_still_consumes_authorization(gateway):
    armed(gateway)
    result = cycle(gateway, '''
run_uhubctl() {
  if [[ "$*" == '-l 1-1' ]]; then
    echo 'Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]'
  else
    printf '%s\\n' "$*" >> "$SYS_ROOT/power-writes"
    return 1
  fi
}
''')
    assert result.returncode != 0
    assert not (gateway / "run/omk-broute-usb/pending").exists()
    assert (gateway / "run/omk-broute-usb/last-cycle").read_text() == "1000\n"
    (gateway / "sys/power-writes").unlink()
    armed(gateway)
    no_power(gateway, cycle(gateway))


def test_root_lock_prevents_overlapping_power_operation(gateway):
    armed(gateway)
    # A second open file description holds the real flock in this process.
    no_power(gateway, cycle(gateway, '''
exec 8<"$STATE_DIR"
flock -n 8
'''))


def test_disappeared_adapter_does_not_bypass_current_hub_guard(gateway):
    armed(gateway)
    (gateway / "sys/bus/usb/devices/1-1.2").unlink()
    (gateway / "sys/devices/1-1/idProduct").write_text("9999")
    no_power(gateway, cycle(gateway))


def test_new_ticket_after_cooldown_can_recover_again(gateway):
    armed(gateway)
    assert cycle(gateway).returncode == 0
    result = run(gateway, extra=ENV + "\nTEST_NOW=4600")
    assert result.returncode == 0, result.stderr
    assert cycle(gateway, "TEST_NOW=4600").returncode == 0
