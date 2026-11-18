"""Real pySerial + local PTY, with cdc_acm control-line semantics modelled.

No USB devices, network, firmware builds, or physical reset tests.
The model verifies the host's sequence, not an ESP32 hardware guarantee.
"""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import termios
from types import SimpleNamespace

import pytest
import serial
from serial.tools import list_ports

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import diag_collect_hostfix as collector
import diag_payload
from test_hostfix_protocol import MockPort, NODE

BOOT = 'f569b46ddd8385ef'


@pytest.fixture
def acm_model(monkeypatch):
    master, slave = os.openpty()
    name = os.ttyname(slave)
    attrs = termios.tcgetattr(slave)
    attrs[2] |= termios.HUPCL
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    real_open, real_close, real_ioctl = os.open, os.close, fcntl.ioctl
    state = SimpleNamespace(bits=0, packets=[], opened=[], closed=[], ioctls=[], path=name,
                            exclusive=False, master=master)
    tracked = set()

    def change(bits, reason):
        # USB encoding: bit 0 DTR, bit 1 RTS. Driver tiocmset skips identical values.
        state.bits = bits
        state.packets.append((bits, reason))

    def open_fd(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(path) == name:
            tracked.add(fd)
            state.opened.append(fd)
            change(3, 'kernel-open')
        return fd

    def close_fd(fd):
        if fd in tracked:
            if termios.tcgetattr(fd)[2] & termios.HUPCL:
                change(0, 'kernel-close-HUPCL')
            state.closed.append(fd)
            tracked.remove(fd)
        return real_close(fd)

    def ioctl(fd, request, arg=0, *rest):
        if fd in tracked:
            state.ioctls.append(request)
            if request in (termios.TIOCMBIS, termios.TIOCMBIC):
                bits = struct.unpack('I', arg)[0]
                mask = (1 if bits & termios.TIOCM_DTR else 0) | (2 if bits & termios.TIOCM_RTS else 0)
                next_bits = state.bits | mask if request == termios.TIOCMBIS else state.bits & ~mask
                if next_bits != state.bits:
                    change(next_bits, 'pyserial-ioctl')
                return 0
            if request == termios.TIOCMGET:
                bits = (termios.TIOCM_DTR if state.bits & 1 else 0) | (termios.TIOCM_RTS if state.bits & 2 else 0)
                return struct.pack('I', bits)
            if request == termios.TIOCEXCL:
                state.exclusive = True
                return 0
        return real_ioctl(fd, request, arg, *rest)

    monkeypatch.setattr(os, 'open', open_fd)
    monkeypatch.setattr(os, 'close', close_fd)
    monkeypatch.setattr(fcntl, 'ioctl', ioctl)
    try:
        yield state
    finally:
        for fd in list(tracked):
            real_close(fd)
        real_close(master)
        real_close(slave)


def test_original_false_false_traverses_reset_state(acm_model):
    port = serial.Serial(port=None)
    port.dtr = False
    port.rts = False
    port.port = acm_model.path
    port.open()
    port.close()
    assert [bits for bits, _ in acm_model.packets] == [3, 2, 0, 0]


def test_candidate_real_pyserial_twenty_open_close_cycles(acm_model):
    for _ in range(20):
        port = collector.open_native_port(acm_model.path, 0.1)
        assert port.dtr and port.rts and not port.rtscts and not port.dsrdtr
        assert not (termios.tcgetattr(port.fileno())[2] & termios.HUPCL)
        assert acm_model.exclusive
        port.close()
    assert len(acm_model.opened) == len(acm_model.closed) == 20
    assert [bits for bits, _ in acm_model.packets] == [3] * 20


def test_candidate_failure_does_not_reopen_or_clear_lines(acm_model, monkeypatch):
    real_ioctl = fcntl.ioctl
    def fail_exclusive(fd, request, arg=0):
        if request == termios.TIOCEXCL:
            raise OSError('exclusive failure')
        return real_ioctl(fd, request, arg)
    monkeypatch.setattr(fcntl, 'ioctl', fail_exclusive)
    with pytest.raises(OSError, match='exclusive failure'):
        collector.open_native_port(acm_model.path, 0.1)
    assert len(acm_model.opened) == len(acm_model.closed) == 1
    assert [bits for bits, _ in acm_model.packets] == [3]


def test_real_pyserial_data_io_does_not_change_control_bits(acm_model):
    port = collector.open_native_port(acm_model.path, 0.1)
    try:
        request = b'{"command":"identify","protocol_version":2}\n'
        assert port.write(request) == len(request)
        assert os.read(acm_model.master, 512) == request
        response = b'{"status":"ok"}\n'
        os.write(acm_model.master, response)
        assert port.readline(512) == response
    finally:
        port.close()
    assert [bits for bits, _ in acm_model.packets] == [3]


def test_hupcl_failure_stops_before_returning_port(acm_model, monkeypatch):
    real_getattr = termios.tcgetattr
    def hupcl_stuck(fd):
        result = real_getattr(fd)
        result[2] |= termios.HUPCL
        return result
    monkeypatch.setattr(termios, 'tcgetattr', hupcl_stuck)
    with pytest.raises(collector.CollectionError, match='HUPCL'):
        collector.open_native_port(acm_model.path, 0.1)
    assert len(acm_model.opened) == len(acm_model.closed) == 1


def test_wrong_line_state_stops_without_repair_pulses(acm_model, monkeypatch):
    real_ioctl = fcntl.ioctl
    def wrong_state(fd, request, arg=0):
        if request == termios.TIOCMGET:
            return struct.pack('I', 0)
        return real_ioctl(fd, request, arg)
    monkeypatch.setattr(fcntl, 'ioctl', wrong_state)
    with pytest.raises(collector.CollectionError, match='unexpected DTR'):
        collector.open_native_port(acm_model.path, 0.1)
    assert [bits for bits, _ in acm_model.packets] == [3]


@pytest.mark.parametrize('platform,version,vid,pid,driver', [
    ('win32','3.5',0x303a,0x1001,'cdc_acm'),
    ('linux','3.6',0x303a,0x1001,'cdc_acm'),
    ('linux','3.5',0x10c4,0xea60,'cp210x'),
    ('linux','3.5',0x303a,0x1001,'other'),
])
def test_metadata_guard_rejects_unsupported_without_open(monkeypatch, platform, version, vid, pid, driver):
    monkeypatch.setattr(collector.sys, 'platform', platform)
    monkeypatch.setattr(serial, 'VERSION', version)
    monkeypatch.setattr(list_ports, 'comports', lambda: [SimpleNamespace(device='/dev/ttyACM99', vid=vid, pid=pid)])
    monkeypatch.setattr(Path, 'resolve', lambda self: Path('/fake')/driver)
    monkeypatch.setattr(serial, 'Serial', lambda **kw: pytest.fail('must not open'))
    with pytest.raises(collector.CollectionError):
        collector.native_usb_port('/dev/ttyACM99')


def payload(boot=BOOT, node=NODE):
    value = diag_payload.Core()
    value.schema, value.bytes = 1, ctypes.sizeof(value)
    value.node_id, value.boot_id = node.encode(), boot.encode()
    value.capture_ms = 723000
    return bytes(value)


def test_live_boot_guard():
    assert collector.check_live_boot(payload(), BOOT, NODE)['capture_ms'] == 723000
    with pytest.raises(collector.CollectionError, match='changed'):
        collector.check_live_boot(payload('9896a54a541639ec'), BOOT, NODE)
    with pytest.raises(collector.CollectionError, match='changed'):
        collector.check_live_boot(payload(node='020000000002'), BOOT, NODE)
    with pytest.raises(collector.CollectionError, match='length'):
        collector.check_live_boot(b'invalid', BOOT, NODE)


@pytest.mark.parametrize('changed,arm', [(False,False),(True,False),(True,True)])
def test_main_preserves_evidence_closes_once_and_refuses_changed_boot(tmp_path, monkeypatch, changed, arm):
    port = MockPort()
    port.live = payload('9896a54a541639ec' if changed else BOOT)
    closed = []
    port.close = lambda: closed.append(True)
    monkeypatch.setattr(collector, 'native_usb_port', lambda path: path)
    monkeypatch.setattr(collector, 'open_native_port', lambda path, timeout: port)
    out = tmp_path/'capture'
    args = ['--port','mock','--node',NODE,'--expected-boot-id',BOOT,'--out-dir',str(out)]
    if arm:
        args += ['--arm','--confirm',collector.CONFIRM]
    if changed:
        with pytest.raises(collector.CollectionError, match='changed'):
            collector.main(args)
        assert not (out/'manifest.json').exists()
    else:
        assert collector.main(args) == 0
        manifest = json.loads((out/'manifest.json').read_text())
        assert manifest['observed_live']['boot_id'] == BOOT
        assert manifest['post_close_boot_verified'] is False
    assert (out/'flash.bin').read_bytes() == port.flash
    assert (out/'live.bin').read_bytes() == port.live
    assert closed == [True]
    assert not any(req['command'] == 'diag_arm' for req in port.requests)
    events = [json.loads(line) for line in (out/'lifecycle.jsonl').read_text().splitlines()]
    assert [e['event'] for e in events][-2:] == ['before_close','after_close']
    assert all(a['monotonic_ns'] <= b['monotonic_ns'] for a,b in zip(events,events[1:]))


def test_open_failure_is_logged_without_collect(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, 'native_usb_port', lambda path: path)
    def fail(*args):
        raise OSError('open failure')
    monkeypatch.setattr(collector, 'open_native_port', fail)
    out = tmp_path/'capture'
    with pytest.raises(OSError, match='open failure'):
        collector.main(['--port','mock','--node',NODE,'--expected-boot-id',BOOT,'--out-dir',str(out)])
    events = [json.loads(line)['event'] for line in (out/'lifecycle.jsonl').read_text().splitlines()]
    assert events == ['before_open','error']
    assert not (out/'flash.bin').exists()


def test_close_still_happens_if_lifecycle_logging_fails(tmp_path, monkeypatch):
    port = MockPort()
    port.live = payload()
    closed = []
    port.close = lambda: closed.append(True)
    monkeypatch.setattr(collector, 'native_usb_port', lambda path: path)
    monkeypatch.setattr(collector, 'open_native_port', lambda path, timeout: port)
    counter = iter(range(1,6))
    def clock():
        value = next(counter)
        if value == 5:
            raise OSError('log unavailable before close')
        return value
    monkeypatch.setattr(collector.time, 'monotonic_ns', clock)
    with pytest.raises(OSError, match='log unavailable'):
        collector.main(['--port','mock','--node',NODE,'--expected-boot-id',BOOT,
                        '--out-dir',str(tmp_path/'capture')])
    assert closed == [True]
