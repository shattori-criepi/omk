"""Exercise real nonblocking PTY fds with deterministic deadline exhaustion."""
import os
import pty
import threading
from types import SimpleNamespace

import pytest

from omk_system_manager import node_provisioning as usb, node_setup as setup
from omk_system_manager.main import scan_usb_candidates_with_serial_lock


@pytest.fixture
def serial_env(monkeypatch):
    master, slave = pty.openpty()
    clock = SimpleNamespace(now=0.0)

    def advance(seconds):
        clock.now += seconds

    monkeypatch.setattr(usb, 'time', SimpleNamespace(monotonic=lambda: clock.now, sleep=advance))
    monkeypatch.setattr(usb.termios, 'tcdrain', lambda *_: pytest.fail('unbounded tcdrain called'))
    opened = []
    original_open = usb.os.open

    def open_serial(*args):
        fd = original_open(*args)
        opened.append(fd)
        assert not os.get_blocking(fd)
        return fd

    monkeypatch.setattr(usb, 'os', SimpleNamespace(**{**vars(os), 'open': open_serial}))
    env = SimpleNamespace(device=os.ttyname(slave), clock=clock, advance=advance, opened=opened)
    yield env
    for fd in opened:
        try:
            os.fstat(fd)
        except OSError:
            continue
        os.close(fd)
    os.close(master)
    os.close(slave)


def assert_closed(env):
    assert env.opened
    for fd in env.opened:
        with pytest.raises(OSError):
            os.fstat(fd)


def set_io(monkeypatch, env, mode):
    def select(readable, writable, exceptional, timeout):
        assert timeout >= 0
        if mode == 'oserror':
            raise OSError('disconnected')
        if writable and mode == 'read_timeout':
            return [], writable, []
        env.advance(timeout)
        return [], [], []
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=select))


@pytest.mark.parametrize('mode', ['read_timeout', 'write_timeout', 'oserror'])
def test_identify_deadline_and_fd_cleanup(monkeypatch, serial_env, mode):
    set_io(monkeypatch, serial_env, mode)
    assert usb.identify(serial_env.device, timeout=3) is None
    assert serial_env.clock.now <= 3
    assert_closed(serial_env)


def test_identify_short_deadline_includes_settle(monkeypatch, serial_env):
    assert usb.identify(serial_env.device, timeout=0.1) is None
    assert serial_env.clock.now == pytest.approx(0.1)
    assert_closed(serial_env)


def test_initialization_error_closes_fd(monkeypatch, serial_env):
    def fail(*args):
        raise usb.termios.error('disconnected')
    monkeypatch.setattr(usb.termios, 'tcsetattr', fail)
    assert usb.identify(serial_env.device, timeout=3) is None
    assert_closed(serial_env)


def test_partial_write_and_would_block_share_response_deadline(monkeypatch, serial_env):
    serial = usb.SerialJson(serial_env.device)
    sent = bytearray()
    waits = []
    calls = 0

    def select(readable, writable, exceptional, timeout):
        waits.append((bool(writable), timeout))
        serial_env.advance(0.1)
        return readable, writable, []

    def write(fd, pending):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BlockingIOError()
        count = min(2, len(pending))
        sent.extend(pending[:count])
        return count

    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=select))
    monkeypatch.setattr(usb.os, 'write', write)
    monkeypatch.setattr(usb.os, 'read', lambda *_: b'{"status":"ok"}\n')
    try:
        assert serial.request({'x': 1}, 3, lambda r: r['status'] == 'ok') == {'status': 'ok'}
        assert sent == b'{"x":1}\n'
        assert waits[0] == (True, 3)
        assert waits[-1][0] is False
        assert waits[-1][1] == pytest.approx(2.5)
    finally:
        serial.close()


def test_repeated_would_block_expires(monkeypatch, serial_env):
    def select(readable, writable, exceptional, timeout):
        serial_env.advance(min(0.1, timeout))
        return [], writable, []
    def blocked(*args):
        raise BlockingIOError()
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=select))
    monkeypatch.setattr(usb.os, 'write', blocked)
    assert usb.identify(serial_env.device, timeout=3) is None
    assert serial_env.clock.now <= 3
    assert_closed(serial_env)


@pytest.mark.parametrize('mode', ['read_timeout', 'write_timeout', 'oserror'])
@pytest.mark.parametrize('mac_timeout', [False, True])
def test_blank_candidate_scan_releases_fd_and_allows_following_setup(monkeypatch, serial_env, tmp_path, mode, mac_timeout):
    set_io(monkeypatch, serial_env, mode)
    monkeypatch.setattr(usb, 'physical_usb_devices', lambda: [(serial_env.device, '/dev/ttyACM0')])
    monkeypatch.setattr(setup, 'STORE', tmp_path)
    inspected = []

    def inspect(command, **kwargs):
        assert_closed(serial_env)
        assert command[-1] == 'read_mac'
        assert kwargs['timeout'] == 15
        inspected.append(command)
        if mac_timeout:
            serial_env.advance(kwargs['timeout'])
            raise setup.subprocess.TimeoutExpired(command, kwargs['timeout'])
        return SimpleNamespace(stdout='Chip is ESP32-S3\nMAC: 00:11:22:33:44:55\n')

    monkeypatch.setattr(setup.subprocess, 'run', inspect)
    serial_lock = threading.Lock()
    serial_lock.acquire()
    candidates = scan_usb_candidates_with_serial_lock(serial_lock, lambda: setup.setup_candidates(timeout=3))
    if mac_timeout:
        assert candidates == []
    else:
        assert len(candidates) == 1
        assert candidates[0]['kind'] == 'unconfirmed_esp32s3'
    assert len(inspected) == 1
    assert serial_env.clock.now <= 18
    assert_closed(serial_env)
    assert not serial_lock.locked()

    # Exercise the actual setup worker's lock acquisition, without hardware writes.
    completed = []
    monkeypatch.setattr(setup, 'setup', lambda *args: completed.append(True))
    operation_lock = threading.Lock()
    operation_lock.acquire()
    controller = setup.SetupController(operation_lock, serial_lock)
    controller.run('/dev/ttyACM0', '55f94c790e12', True)
    assert completed == [True]
    assert controller.status()['stage'] == 'completed'
    assert not serial_lock.locked()
    assert not operation_lock.locked()
