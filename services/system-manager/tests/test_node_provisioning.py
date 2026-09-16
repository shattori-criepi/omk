"""Exercise real nonblocking PTY fds with deterministic deadline exhaustion."""
import json
import os
import pty
import threading
from types import SimpleNamespace

import pytest
from omk_system_manager import node_provisioning as usb
from omk_system_manager import node_setup as setup
from omk_system_manager.main import scan_usb_candidates_with_serial_lock


@pytest.fixture
def serial_env(monkeypatch):
    master, slave = pty.openpty()
    clock = SimpleNamespace(now=0.0)

    def advance(seconds):
        clock.now += seconds

    monkeypatch.setattr(usb, 'time', SimpleNamespace(monotonic=lambda: clock.now, sleep=advance))
    monkeypatch.setattr(usb.termios, 'tcdrain', lambda *_: pytest.fail('unbounded tcdrain called'))
    flushed = []
    def output_flush(fd, selector):
        assert selector == usb.termios.TCOFLUSH, 'input flush must remain nonblocking reads'
        os.fstat(fd)
        flushed.append(fd)
    monkeypatch.setattr(usb.termios, 'tcflush', output_flush)
    opened = []
    original_open = usb.os.open

    def open_serial(*args):
        fd = original_open(*args)
        opened.append(fd)
        assert not os.get_blocking(fd)
        return fd

    monkeypatch.setattr(usb, 'os', SimpleNamespace(**{**vars(os), 'open': open_serial}))
    env = SimpleNamespace(device=os.ttyname(slave), master=master, clock=clock,
                          advance=advance, opened=opened, flushed=flushed)
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
        if timeout == 0:
            return [], [], []
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
        assert serial_env.flushed == []
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
        assert serial_env.clock.now <= 3
        assert command[-1] == 'read_mac'
        if mode != 'oserror':
            assert serial_env.flushed == serial_env.opened
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
        assert candidates[0]['node_id'] == setup.node_id_from_mac('00:11:22:33:44:55')
        assert candidates[0]['wifi_configured'] is False
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


def test_discard_empty_input_returns_immediately(monkeypatch, serial_env):
    waits = []
    def empty(readable, writable, exceptional, timeout):
        waits.append(timeout)
        return [], [], []
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=empty))
    monkeypatch.setattr(usb.os, 'read', lambda *_: pytest.fail('empty input was read'))
    usb._discard_pending_input(123, deadline=3)
    assert waits == [0]
    assert serial_env.clock.now == 0


def test_discard_would_block_returns_immediately(monkeypatch, serial_env):
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=lambda r, w, x, t: (r, [], [])))
    def blocked(*args):
        raise BlockingIOError()
    monkeypatch.setattr(usb.os, 'read', blocked)
    usb._discard_pending_input(123, deadline=3)
    assert serial_env.clock.now == 0


@pytest.mark.parametrize('limit', ['bytes', 'time', 'shared_deadline'])
def test_continuous_input_fails_closed_and_closes_fd(monkeypatch, serial_env, limit):
    serial = usb.SerialJson(serial_env.device)
    received = []
    deadline = 0.02 if limit == 'shared_deadline' else 3
    started = serial_env.clock.now
    def readable(r, w, x, timeout):
        assert timeout == 0
        return r, [], []
    def read(fd, size):
        received.append(size)
        if limit != 'bytes':
            serial_env.advance(0.01)
        return b'x' * size
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=readable))
    monkeypatch.setattr(usb.os, 'read', read)
    monkeypatch.setattr(usb.os, 'write', lambda *_: pytest.fail('request sent with stale input'))
    try:
        with pytest.raises(TimeoutError, match='input discard limit'):
            serial.request({'command': 'identify'}, deadline, lambda _: True)
    finally:
        serial.close()
    assert sum(received) <= usb.INPUT_DISCARD_MAX_BYTES
    assert serial_env.flushed == []  # Failure before any write attempt.
    assert serial_env.clock.now - started <= min(deadline, usb.INPUT_DISCARD_TIMEOUT_SECONDS) + 1e-9
    if limit == 'bytes':
        assert sum(received) == usb.INPUT_DISCARD_MAX_BYTES
    assert_closed(serial_env)


@pytest.mark.parametrize('failure', ['continuous', 'eio', 'eof'])
def test_discard_initialization_failure_closes_fd(monkeypatch, serial_env, failure):
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=lambda r, w, x, t: (r, [], [])))
    def read(fd, size):
        if failure == 'eio':
            raise OSError(5, 'Input/output error')
        return b'x' * size if failure == 'continuous' else b''
    monkeypatch.setattr(usb.os, 'read', read)
    assert usb.identify(serial_env.device, timeout=3) is None
    assert serial_env.clock.now <= 3
    assert_closed(serial_env)


def test_pending_stale_replies_discarded_before_each_request(monkeypatch, serial_env):
    stale = b'{"status":"ok","node_id":"000000000001","protocol_version":2}\n'
    fresh = {'status': 'ok', 'node_id': '000000000002', 'protocol_version': 2}
    # Queue input during settle, after raw mode has been configured.
    def settle(seconds):
        serial_env.advance(seconds)
        os.write(serial_env.master, stale)
    monkeypatch.setattr(usb.time, 'sleep', settle)
    serial = usb.SerialJson(serial_env.device)
    real_select = usb.select.select
    assert real_select([serial.fd], [], [], 0)[0] == []
    original_write = usb.os.write
    def respond(fd, data):
        assert real_select([fd], [], [], 0)[0] == []
        written = original_write(fd, data)
        original_write(serial_env.master, (json.dumps(fresh) + '\n').encode())
        return written
    monkeypatch.setattr(usb.os, 'write', respond)
    try:
        for _ in range(2):
            original_write(serial_env.master, stale)
            serial._buffer = stale
            assert serial.request({'command': 'identify'}, 3, usb.is_protocol_response) == fresh
    finally:
        serial.close()
    assert_closed(serial_env)


def test_existing_node_identify_uses_fresh_response_without_inspection(monkeypatch, serial_env, tmp_path):
    identity = {'status': 'ok', 'node_id': '000000000002', 'protocol_version': 2, 'wifi_configured': True}
    original_write = usb.os.write
    def respond(fd, data):
        assert json.loads(bytes(data))['command'] == 'identify'
        written = original_write(fd, data)
        original_write(serial_env.master, (json.dumps(identity) + '\n').encode())
        return written
    monkeypatch.setattr(usb.os, 'write', respond)
    monkeypatch.setattr(usb, 'physical_usb_devices', lambda: [(serial_env.device, '/dev/ttyACM0')])
    monkeypatch.setattr(setup, 'read_mac', lambda *_: pytest.fail('identified OMK Node inspected'))
    monkeypatch.setattr(setup, 'STORE', tmp_path)
    assert setup.setup_candidates(timeout=3) == [{'device': '/dev/ttyACM0', 'node_id': identity['node_id'],
                                                      'kind': 'omk_node', 'wifi_configured': True, 'credential_state': 'missing'}]
    assert_closed(serial_env)


def test_unresponsive_pty_identify_obeys_wall_clock_timeout(monkeypatch, serial_env):
    import time
    monkeypatch.setattr(usb, 'time', time)
    started = time.monotonic()
    assert usb.identify(serial_env.device, timeout=3) is None
    elapsed = time.monotonic() - started
    assert 2.9 <= elapsed <= 3.5
    assert_closed(serial_env)


@pytest.mark.parametrize('failure', ['timeout', 'read_error', 'disconnect', 'partial_write'])
@pytest.mark.parametrize('cleanup_error', [None, OSError, usb.termios.error])
def test_failed_request_flushes_output_and_preserves_failure(monkeypatch, serial_env, failure, cleanup_error):
    serial = usb.SerialJson(serial_env.device)
    original = OSError('request I/O failed')
    writes = []
    events = []
    def select(readable, writable, exceptional, timeout):
        if timeout == 0:
            return [], [], []
        if readable and failure == 'timeout':
            serial_env.advance(timeout)
            return [], [], []
        return readable, writable, []
    def write(fd, data):
        if writes and failure == 'partial_write':
            raise original
        writes.append(bytes(data[:2]))
        return 2
    def read(fd, size):
        if failure == 'read_error':
            raise original
        assert failure == 'disconnect'
        return b''
    def flush(fd, selector):
        assert writes
        assert fd == serial.fd
        assert selector == usb.termios.TCOFLUSH
        os.fstat(fd)
        events.append('flush')
        if cleanup_error:
            raise cleanup_error(5, 'cleanup failed')
    real_close = usb.os.close
    def close(fd):
        events.append('close')
        real_close(fd)
    monkeypatch.setattr(usb, 'select', SimpleNamespace(select=select))
    monkeypatch.setattr(usb.os, 'write', write)
    monkeypatch.setattr(usb.os, 'read', read)
    monkeypatch.setattr(usb.os, 'close', close)
    monkeypatch.setattr(usb.termios, 'tcflush', flush)
    try:
        with pytest.raises(TimeoutError if failure == 'timeout' else OSError) as caught:
            serial.request({'command': 'identify'}, 1, lambda _: True)
        if failure in {'read_error', 'partial_write'}:
            assert caught.value is original
        elif failure == 'timeout':
            assert 'No OMK USB provisioning response' in str(caught.value)
        else:
            assert 'device disconnected' in str(caught.value)
    finally:
        serial.close()
    assert events == ['flush', 'close']
    assert_closed(serial_env)


@pytest.fixture
def queued_output(monkeypatch, serial_env):
    pending = bytearray()
    flushes = []
    real_close = usb.os.close
    def write(fd, data):
        pending.extend(data)
        return len(data)
    def flush(fd, selector):
        assert selector == usb.termios.TCOFLUSH
        os.fstat(fd)
        pending.clear()
        flushes.append(fd)
    def close(fd):
        if pending:
            serial_env.advance(30)  # Reproduce the blank CDC close wait.
        real_close(fd)
    monkeypatch.setattr(usb.os, 'write', write)
    monkeypatch.setattr(usb.os, 'close', close)
    monkeypatch.setattr(usb.termios, 'tcflush', flush)
    set_io(monkeypatch, serial_env, 'read_timeout')
    return SimpleNamespace(pending=pending, flushes=flushes)


def test_failed_output_is_not_carried_to_next_request(serial_env, queued_output):
    serial = usb.SerialJson(serial_env.device)
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                serial.request({'command': 'identify'}, 1, lambda _: True)
            assert queued_output.pending == b''
        assert queued_output.flushes == [serial.fd, serial.fd]
    finally:
        serial.close()
    assert serial_env.clock.now == pytest.approx(2.5)
    assert_closed(serial_env)


def test_repeated_blank_scans_flush_before_close_and_inspection(monkeypatch, serial_env, queued_output, tmp_path):
    monkeypatch.setattr(usb, 'physical_usb_devices', lambda: [(serial_env.device, '/dev/ttyACM0')])
    monkeypatch.setattr(setup, 'STORE', tmp_path)
    inspected = []
    def inspect(command, **kwargs):
        assert command[-1] == 'read_mac'
        assert_closed(serial_env)
        assert not queued_output.pending
        assert queued_output.flushes == serial_env.opened
        inspected.append(command)
        return SimpleNamespace(stdout='Chip is ESP32-S3\nMAC: 00:11:22:33:44:55\n')
    monkeypatch.setattr(setup.subprocess, 'run', inspect)
    serial_lock = threading.Lock()
    for _ in range(3):
        started = serial_env.clock.now
        assert serial_lock.acquire(blocking=False)
        candidates = scan_usb_candidates_with_serial_lock(serial_lock, lambda: setup.setup_candidates(timeout=3))
        assert serial_env.clock.now - started == pytest.approx(3)
        assert candidates == [{'device': '/dev/ttyACM0', 'node_id': setup.node_id_from_mac('00:11:22:33:44:55'),
                               'kind': 'unconfirmed_esp32s3', 'wifi_configured': False, 'credential_state': 'missing'}]
        assert not serial_lock.locked()
    assert len(inspected) == 3


def test_successful_wifi_provisioning_never_flushes_output(monkeypatch, serial_env):
    node_id = '000000000002'
    commands = []
    real_write = usb.os.write
    def respond(fd, data):
        request = json.loads(bytes(data))
        commands.append(request['command'])
        response = {'status': 'ok' if request['command'] == 'identify' else 'accepted',
                    'node_id': node_id, 'protocol_version': 2}
        written = real_write(fd, data)
        real_write(serial_env.master, (json.dumps(response) + '\n').encode())
        return written
    monkeypatch.setattr(usb.os, 'write', respond)
    assert usb.provision(serial_env.device, 'test-ssid', 'test-password', expected_node_id=node_id) == node_id
    assert commands == ['identify', 'set_wifi']
    assert serial_env.flushed == []
    assert_closed(serial_env)
