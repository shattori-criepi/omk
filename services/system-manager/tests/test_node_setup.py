from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from omk_system_manager import node_setup as setup
from omk_system_manager.node_provisioning import ProvisioningError

RUN_ESPTOOL = setup.esptool
READ_MAC = setup.read_mac
MAC = '02:00:00:00:00:01'
NODE = setup.node_id_from_mac(MAC)
DEVICE = '/dev/serial/by-id/usb-espressif-test'


@pytest.fixture(autouse=True)
def forbid_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Real serial/subprocess access forbidden')
    monkeypatch.setattr(setup.subprocess, 'run', forbidden)
    monkeypatch.setattr(setup.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(setup.usb, 'SerialJson', forbidden)
    monkeypatch.setattr(setup.usb, 'physical_usb_devices', lambda: [(DEVICE, '/dev/ttyACM0')])


@pytest.fixture
def package(tmp_path):
    path = tmp_path / 'package'
    path.mkdir()
    segments = []
    for filename, offset, _ in setup.SEGMENTS:
        data = (filename * 5).encode()
        (path / filename).write_bytes(data)
        segments.append(dict(filename=filename, offset=offset, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    (path / 'manifest.json').write_text(json.dumps(dict(schema_version=1, target='atom-s3-lite', chip='esp32s3', source_commit='a'*40, segments=segments)))
    return path


@pytest.fixture
def hardware(monkeypatch):
    calls = []
    monkeypatch.setattr(setup.usb, 'identify', lambda *a, **kw: dict(node_id=NODE, protocol_version=2))
    monkeypatch.setattr(setup, 'read_mac', lambda device, **kw: MAC)
    monkeypatch.setattr(setup, 'wait_for_node', lambda *a: None)
    monkeypatch.setattr(setup.usb, 'read_gateway_wifi', lambda: ('OMK', 'private-psk'))
    monkeypatch.setattr(setup.usb, 'provision', lambda *a, **kw: NODE)
    monkeypatch.setattr(setup.usb, 'wait_for_registration_status', lambda *a, **kw: None)
    def tool(device, arguments, code):
        calls.append((arguments, code))
        return ''
    monkeypatch.setattr(setup, 'esptool', tool)
    return calls


def saved_credential(tmp_path):
    credential = tmp_path/'store/nodes'/f'{NODE}.json'
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text(json.dumps(dict(node_id=NODE, board='atom-s3-lite', provisioning_secret='ab'*32)))
    return credential


def execute(package, tmp_path, confirmed=False):
    setup.setup(DEVICE, NODE, confirmed, lambda stage: None, package=package, store=tmp_path/'store/nodes')


@pytest.mark.parametrize('mutation', ['missing', 'json', 'schema', 'target', 'chip', 'commit', 'filename', 'offset', 'extra', 'missing_segment', 'hash', 'size', 'oversize', 'symlink', 'directory', 'manifest_symlink', 'package_symlink', 'duplicate', 'absolute', 'missing_binary', 'duplicate_segment'])
def test_invalid_package_never_touches_usb(package, tmp_path, monkeypatch, mutation):
    manifest = package/'manifest.json'
    data = json.loads(manifest.read_text())
    if mutation == 'missing': manifest.unlink()
    elif mutation == 'json': manifest.write_text('{')
    elif mutation in ('schema', 'target', 'chip', 'commit'):
        key = dict(schema='schema_version', target='target', chip='chip', commit='source_commit')[mutation]
        data[key] = 'invalid'
        manifest.write_text(json.dumps(data))
    elif mutation in ('filename', 'offset', 'hash', 'size'):
        key = dict(filename='filename', offset='offset', hash='sha256', size='size')[mutation]
        data['segments'][0][key] = {'filename':'../bootloader.bin', 'offset':'0xf000', 'sha256':'0'*64, 'size':999}[key]
        manifest.write_text(json.dumps(data))
    elif mutation in ('extra', 'missing_segment'):
        if mutation == 'extra': data['segments'].append(data['segments'][0])
        else: data['segments'].pop()
        manifest.write_text(json.dumps(data))
    elif mutation == 'oversize': (package/'partitions.bin').write_bytes(b'x'*4097)
    elif mutation in ('symlink', 'directory'):
        target = package/'firmware.bin'; target.unlink()
        if mutation == 'symlink': target.symlink_to(package/'bootloader.bin')
        else: target.mkdir()
    elif mutation == 'manifest_symlink':
        copied = tmp_path/'manifest'; copied.write_text(manifest.read_text()); manifest.unlink(); manifest.symlink_to(copied)
    elif mutation == 'package_symlink':
        alias = tmp_path/'alias'; alias.symlink_to(package, target_is_directory=True); package = alias
    elif mutation == 'absolute':
        data['segments'][0]['filename'] = str(package/'bootloader.bin')
        manifest.write_text(json.dumps(data))
    elif mutation == 'missing_binary': (package/'firmware.bin').unlink()
    elif mutation == 'duplicate_segment':
        data['segments'][1] = data['segments'][0]
        manifest.write_text(json.dumps(data))
    elif mutation == 'duplicate': manifest.write_text(manifest.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: pytest.fail('USB touched'))
    with pytest.raises(ProvisioningError): execute(package, tmp_path)


def test_existing_missing_credential_requires_explicit_reinitialize(package, tmp_path, hardware):
    with pytest.raises(ProvisioningError, match='reinitialize_required'):
        execute(package, tmp_path)
    assert hardware == []
    assert not (tmp_path/'store').exists()


def test_setup_uses_long_fresh_registration_wait(package, tmp_path, hardware, monkeypatch):
    saved_credential(tmp_path)
    waited = []

    def wait_for_registration(node_id, **kwargs):
        waited.append((node_id, kwargs))

    monkeypatch.setattr(setup.usb, 'wait_for_registration_status', wait_for_registration)
    execute(package, tmp_path)
    assert waited == [(
        NODE,
        {'timeout': setup.MQTT_REGISTRATION_TIMEOUT_SECONDS, 'fresh': True},
    )]
    assert setup.MQTT_REGISTRATION_TIMEOUT_SECONDS == 120


def test_existing_credential_is_unchanged(package, tmp_path, hardware):
    credential = saved_credential(tmp_path)
    original = credential.read_bytes()
    execute(package, tmp_path, True)
    assert len(hardware) == 1 and credential.read_bytes() == original


def test_new_confirmation_and_recovery(package, tmp_path, hardware, monkeypatch):
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: None)
    with pytest.raises(ProvisioningError, match='confirmation'): execute(package, tmp_path)
    assert hardware == [] and not (tmp_path/'store').exists()
    credential = tmp_path/'store/nodes'/f'{NODE}.json'; credential.parent.mkdir(parents=True); credential.write_text('{}')
    with pytest.raises(ProvisioningError, match='recovery'): execute(package, tmp_path, True)
    assert hardware == [] and credential.read_text() == '{}'


def test_new_factory_record_and_credential(package, tmp_path, hardware, monkeypatch):
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: None)
    writes = []
    def tool(device, args, code):
        writes.append(args)
        if code == 'factory_write_failed':
            record = Path(args[-1]).read_bytes()
            credential = tmp_path/'store/nodes'/f'{NODE}.json'
            value = json.loads(credential.read_text())
            # Assert booleans/lengths so pytest does not print secret bytes.
            record_matches = record == b'OMKP' + bytes.fromhex(value['provisioning_secret'])
            record_length = len(record)
            assert record_matches, 'factory record does not match the saved credential'
            assert record_length == 36 and credential.stat().st_mode & 0o777 == 0o600
        return ''
    monkeypatch.setattr(setup, 'esptool', tool)
    execute(package, tmp_path, True)
    assert len(writes) == 2 and writes[0][-2] == '0xf000'


@pytest.mark.parametrize('existing,boundary', [(False,1), (False,2), (False,3), (True,1)])
def test_mac_change_at_write_boundary(package, tmp_path, hardware, monkeypatch, existing, boundary):
    if existing: saved_credential(tmp_path)
    else: monkeypatch.setattr(setup.usb, 'identify', lambda *a: None)
    calls = 0
    def mac(device, **kw):
        nonlocal calls
        calls += 1
        return MAC if calls <= boundary else '02:00:00:00:00:02'
    monkeypatch.setattr(setup, 'read_mac', mac)
    with pytest.raises(ProvisioningError, match='identity_changed'): execute(package, tmp_path, True)
    attempted_factory = not existing and boundary == 3
    assert len(hardware) == int(attempted_factory)
    assert (tmp_path/'store/nodes'/f'{NODE}.json').exists() == (existing or attempted_factory)


def test_disappears_before_write(package, tmp_path, hardware, monkeypatch):
    saved_credential(tmp_path)
    values = iter([True, False]); monkeypatch.setattr(setup, 'allowed', lambda device: next(values))
    with pytest.raises(ProvisioningError, match='not_available'): execute(package, tmp_path)
    assert hardware == []


def test_protocol_efuse_mismatch(package, tmp_path, hardware, monkeypatch):
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: dict(node_id='f'*12))
    with pytest.raises(ProvisioningError, match='identity_changed'): execute(package, tmp_path)
    assert hardware == []


@pytest.mark.parametrize('failure', ['factory_write_failed', 'firmware_write_failed', 'node_reappearance_timeout', 'set_wifi_failed', 'mqtt_registration_timeout'])
def test_failures_retain_attempted_credential(package, tmp_path, hardware, monkeypatch, failure):
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: None)
    def fail(*a, **kw): raise ProvisioningError(failure)
    if failure.endswith('write_failed'):
        def tool(device, args, code):
            if code == failure: fail()
            return ''
        monkeypatch.setattr(setup, 'esptool', tool)
    elif failure == 'node_reappearance_timeout': monkeypatch.setattr(setup, 'wait_for_node', fail)
    elif failure == 'set_wifi_failed': monkeypatch.setattr(setup.usb, 'provision', fail)
    else: monkeypatch.setattr(setup.usb, 'wait_for_registration_status', fail)
    with pytest.raises(ProvisioningError, match=failure): execute(package, tmp_path, True)
    assert (tmp_path/'store/nodes'/f'{NODE}.json').exists()


@pytest.mark.parametrize('output,code', [('Chip is ESP32\nMAC: '+MAC, 'unsupported_chip'), ('Chip is ESP32-S3\nMAC: '+MAC+'\nMAC: 02:00:00:00:00:02','ambiguous_mac'), ('Chip is ESP32-S3\nMAC: malformed','ambiguous_mac')])
def test_read_mac_rejects_unsupported_and_ambiguous(monkeypatch, output, code):
    monkeypatch.setattr(setup, 'esptool', lambda *a: output)
    with pytest.raises(ProvisioningError, match=code): setup.read_mac(DEVICE)


def test_esptool_suppresses_raw_output(monkeypatch, caplog):
    secret = 'private-secret-psk '+MAC
    def fail(args, **kwargs):
        assert kwargs['capture_output'] and not kwargs.get('shell')
        assert args[:3] == [setup.sys.executable, '-m', 'esptool']
        raise subprocess.CalledProcessError(2, args, output=secret, stderr=secret)
    monkeypatch.setattr(setup.subprocess, 'run', fail)
    with pytest.raises(ProvisioningError) as error: setup.esptool(DEVICE, ['read_mac'], 'device_inspection_failed')
    assert secret not in str(error.value) + caplog.text


def test_candidate_kinds_and_no_ttyusb(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, 'STORE', tmp_path)
    monkeypatch.setattr(setup.usb, 'physical_usb_devices', lambda: [(DEVICE,'/dev/ttyACM0'), ('/dev/serial/by-id/espressif-ftdi','/dev/ttyUSB0')])
    probes=[]
    monkeypatch.setattr(setup.usb, 'identify', lambda device, timeout: probes.append(device) or None)
    monkeypatch.setattr(setup, 'read_mac', lambda device, **kw: MAC)
    assert setup.setup_candidates()[0]['kind'] == 'unconfirmed_esp32s3'
    (tmp_path/f'{NODE}.json').write_text('{}')
    assert setup.setup_candidates()[0]['kind'] == 'recovery_required'
    assert probes == [DEVICE, DEVICE]
    assert not setup.allowed('/dev/serial/by-id/espressif-ftdi')


def test_job_lock_lifecycle_and_sanitized_failure(monkeypatch):
    entered = threading.Event(); release = threading.Event()
    def work(*args):
        entered.set(); assert release.wait(3)
        raise RuntimeError('private-psk '+MAC)
    monkeypatch.setattr(setup, 'setup', work)
    operation = threading.Lock(); serial = threading.Lock()
    controller = setup.SetupController(operation, serial)
    assert controller.start(DEVICE, NODE, False)
    assert entered.wait(3) and operation.locked() and serial.locked()
    assert not controller.start(DEVICE, NODE, False)
    release.set(); controller.worker.join(3)
    assert controller.status() == dict(stage='failed', node_id=NODE, error='setup_failed')
    assert not operation.locked() and not serial.locked()


def test_shipped_package_valid():
    assert set(setup.validate_package()) == {item[0] for item in setup.SEGMENTS}


@pytest.mark.parametrize('response,code', [(None, 'node_reappearance_timeout'), ({'node_id': 'f'*12}, 'node_identity_changed'), ({'node_id': NODE, 'protocol_version': 1}, 'unsupported_usb_protocol')])
def test_wait_for_same_node_never_follows_other_ports(monkeypatch, response, code):
    now = [0]
    monkeypatch.setattr(setup.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(setup.time, 'sleep', lambda seconds: now.__setitem__(0, now[0]+seconds))
    probes = []
    monkeypatch.setattr(setup.usb, 'identify', lambda device, **kw: probes.append(device) or response)
    with pytest.raises(ProvisioningError, match=code): setup.wait_for_node(DEVICE, NODE, timeout=3)
    assert probes and set(probes) == {DEVICE}


def test_snapshot_prevents_package_replacement_after_validation(package, tmp_path, hardware, monkeypatch):
    saved_credential(tmp_path)
    original = (package/'firmware.bin').read_bytes()
    def identity(*a):
        (package/'firmware.bin').write_bytes(b'replaced-after-validation')
        return {'node_id': NODE}
    monkeypatch.setattr(setup.usb, 'identify', identity)
    def tool(device, args, code):
        assert Path(args[-1]).read_bytes() == original
        return ''
    monkeypatch.setattr(setup, 'esptool', tool)
    execute(package, tmp_path)


def test_registration_wait_ignores_retained_and_handles_timeout(monkeypatch):
    commands = []
    class Process:
        returncode = 1
        def communicate(self, **kwargs): return ('', '')
    monkeypatch.setattr(setup.subprocess, 'Popen', lambda command, **kw: commands.append(command) or Process())
    with pytest.raises(ProvisioningError, match='mqtt_registration_timeout'):
        setup.usb.wait_for_registration_status(NODE, timeout=120, fresh=True)
    assert '-R' in commands[0] and commands[0][-1] == f'omk/node/{NODE}/registration/status'
    assert commands[0][commands[0].index('-W') + 1] == '120'


def test_registration_wait_accepts_fresh_matching_status(monkeypatch):
    class Process:
        returncode = 0

        def communicate(self, **kwargs):
            return (json.dumps({'node_id': NODE, 'registration_state': 'provisioned'}), '')

    command = []
    monkeypatch.setattr(setup.subprocess, 'Popen', lambda args, **kwargs: command.extend(args) or Process())
    setup.usb.wait_for_registration_status(NODE, timeout=120, fresh=True)
    assert '-R' in command and command[command.index('-W') + 1] == '120'


def test_write_boundary_inspection_keeps_bootloader_alive(monkeypatch):
    commands = []
    def tool(device, arguments, code):
        commands.append(arguments)
        return 'Chip is ESP32-S3 (QFN56)\nMAC: '+MAC
    monkeypatch.setattr(setup, 'esptool', tool)
    setup.confirm_identity(DEVICE, MAC)
    setup.confirm_identity(DEVICE, MAC, stay_in_bootloader=False)
    assert commands == [['--after', 'no_reset', 'read_mac'], ['read_mac']]
    assert setup.node_id_from_mac('02:00:00:00:00:ab') == '6e6005821cda'


def test_inventory_does_not_publish_mac_in_by_id_path(monkeypatch):
    private_path = '/dev/serial/by-id/usb-Espressif_' + MAC
    monkeypatch.setattr(setup.usb, 'physical_usb_devices', lambda: [(private_path, '/dev/ttyACM0')])
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: {'node_id': NODE})
    candidates = setup.setup_candidates()
    assert MAC not in json.dumps(candidates)
    assert candidates[0]['device'] == '/dev/ttyACM0'


def test_public_tty_resolves_to_stable_allowlisted_path(package, tmp_path, hardware, monkeypatch):
    saved_credential(tmp_path)
    devices = []
    def tool(device, arguments, code):
        devices.append(device)
        # The stable by-id path now reappears on a different tty number.
        monkeypatch.setattr(setup.usb, 'physical_usb_devices', lambda: [(DEVICE, '/dev/ttyACM1')])
        hardware.append(arguments)
        return ''
    monkeypatch.setattr(setup, 'esptool', tool)
    setup.setup('/dev/ttyACM0', NODE, False, lambda stage: None, package=package, store=tmp_path/'store/nodes')
    assert len(hardware) == 1 and devices == [DEVICE]


@pytest.mark.parametrize('output,code', [('Chip is ESP32\nMAC: '+MAC, 'unsupported_chip'), ('Chip is ESP32-S3\nMAC: '+MAC+'\nMAC: 02:00:00:00:00:02', 'ambiguous_mac')])
def test_chip_or_mac_inspection_failure_stops_all_writes(package, tmp_path, hardware, monkeypatch, output, code):
    commands = []
    monkeypatch.setattr(setup, 'read_mac', READ_MAC)
    monkeypatch.setattr(setup, 'esptool', lambda device, args, failure: commands.append(args) or output)
    with pytest.raises(ProvisioningError, match=code): execute(package, tmp_path, True)
    assert commands and not any('write_flash' in args for args in commands)
    assert not (tmp_path/'store').exists()


def test_hash_mismatch_stops_all_esptool_calls(package, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(setup, 'esptool', lambda *a: calls.append(a))
    (package/'firmware.bin').write_bytes(b'corrupt')
    with pytest.raises(ProvisioningError, match='firmware_package_invalid'): execute(package, tmp_path, True)
    assert calls == []


def test_existing_setup_actual_subprocess_argv_is_fixed(package, tmp_path, hardware, monkeypatch, caplog):
    saved_credential(tmp_path)
    commands = []
    monkeypatch.setattr(setup, 'esptool', RUN_ESPTOOL)
    monkeypatch.setattr(setup, 'read_mac', READ_MAC)
    def run(argv, **kwargs):
        commands.append(argv)
        assert argv[:5] == [setup.sys.executable, '-m', 'esptool', '--port', DEVICE]
        assert kwargs['capture_output'] and kwargs['check'] and not kwargs.get('shell', False)
        return subprocess.CompletedProcess(argv, 0, 'Chip is ESP32-S3\nMAC: '+MAC, '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    execute(package, tmp_path)
    writes = [argv for argv in commands if 'write_flash' in argv]
    assert len(writes) == 1
    assert writes[0][5:8] == ['--chip', 'esp32s3', 'write_flash']
    assert writes[0][8::2] == ['0x00000000', '0x00008000', '0x00010000']
    assert [Path(value).name for value in writes[0][9::2]] == ['bootloader.bin', 'partitions.bin', 'firmware.bin']
    assert all('0xf000' not in argv and '0x9000' not in argv and 'erase_flash' not in argv for argv in commands)
    assert MAC not in caplog.text and 'private-psk' not in str(commands)
    assert (tmp_path/'store/nodes'/f'{NODE}.json').exists()


@pytest.fixture
def reset_package(package):
    firmware = package/'firmware.bin'
    firmware.write_bytes(firmware.read_bytes() + b'verify_reinitialize\x00')
    manifest = package/'manifest.json'
    value = json.loads(manifest.read_text())
    value['segments'][2].update(size=firmware.stat().st_size, sha256=hashlib.sha256(firmware.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(value))
    return package


def reinitialize(package, tmp_path, confirmed=True):
    setup.setup(DEVICE, NODE, confirmed, lambda stage: None, package=package,
                store=tmp_path/'store/nodes', reinitialize=True)


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('responds', [False, True])
def test_reinitialize_replaces_pop_verifies_node_and_provisions_new_gateway(reset_package, tmp_path, hardware, monkeypatch, existing, responds):
    if not responds: monkeypatch.setattr(setup.usb, 'identify', lambda *a: None)
    if existing: saved_credential(tmp_path)
    events = []
    store = tmp_path/'store/nodes'
    def tool(device, args, code):
        assert device == DEVICE
        events.append(code)
        assert 'erase_flash' not in args
        assert '0x9000' not in args
        if code == 'factory_write_failed':
            saved = setup.read_credential(store, NODE)
            assert saved['setup_state'] == 'reinitialize_pending'
            assert saved['provisioning_secret'] != 'ab'*32
            assert Path(args[-1]).read_bytes() == b'OMKR' + bytes.fromhex(saved['provisioning_secret'])
            assert (store/f'{NODE}.json').stat().st_mode & 0o777 == 0o600
        return ''
    monkeypatch.setattr(setup, 'esptool', tool)
    def verify(device, secret, *, expected_node_id):
        assert device == DEVICE and expected_node_id == NODE
        assert secret == setup.read_credential(store, NODE)['provisioning_secret']
        events.append('verify')
    def provision(device, ssid, psk, *, expected_node_id):
        assert expected_node_id == NODE and (ssid, psk) == ('NEW-AP', 'new-synthetic-password')
        assert events[-1] == 'verify'
        events.append('wifi')
    monkeypatch.setattr(setup.usb, 'verify_reinitialize', verify)
    monkeypatch.setattr(setup.usb, 'read_gateway_wifi', lambda: ('NEW-AP', 'new-synthetic-password'))
    monkeypatch.setattr(setup.usb, 'provision', provision)
    def wait(node_id, **kwargs):
        assert node_id == NODE and kwargs['expected_state'] == 'provisioned' and kwargs['fresh']
        events.append('mqtt')
    monkeypatch.setattr(setup.usb, 'wait_for_registration_status', wait)
    reinitialize(reset_package, tmp_path)
    assert events == ['factory_write_failed', 'firmware_write_failed', 'verify', 'wifi', 'mqtt']
    assert 'setup_state' not in setup.read_credential(store, NODE)


@pytest.mark.parametrize('failure', ['factory_write_failed', 'firmware_write_failed', 'node_reappearance_timeout', 'verification_failed', 'set_wifi_failed', 'mqtt_registration_timeout'])
def test_reinitialize_partial_failure_reuses_same_durable_credential_on_retry(reset_package, tmp_path, hardware, monkeypatch, failure):
    original_tool = setup.esptool
    original_wait = setup.wait_for_node
    original_wifi = setup.usb.provision
    original_mqtt = setup.usb.wait_for_registration_status
    def fail(*args, **kwargs): raise ProvisioningError(failure)
    monkeypatch.setattr(setup.usb, 'verify_reinitialize', lambda *a, **kw: NODE)
    if failure.endswith('write_failed'):
        def tool(device, args, code):
            if code == failure: fail()
            return ''
        monkeypatch.setattr(setup, 'esptool', tool)
    elif failure == 'node_reappearance_timeout': monkeypatch.setattr(setup, 'wait_for_node', fail)
    elif failure == 'verification_failed': monkeypatch.setattr(setup.usb, 'verify_reinitialize', fail)
    elif failure == 'set_wifi_failed': monkeypatch.setattr(setup.usb, 'provision', fail)
    else: monkeypatch.setattr(setup.usb, 'wait_for_registration_status', fail)
    with pytest.raises(ProvisioningError, match=failure): reinitialize(reset_package, tmp_path)
    pending = setup.read_credential(tmp_path/'store/nodes', NODE)
    assert pending['setup_state'] == 'reinitialize_pending'
    with pytest.raises(ProvisioningError): execute(reset_package, tmp_path)
    monkeypatch.setattr(setup, 'esptool', original_tool)
    monkeypatch.setattr(setup, 'wait_for_node', original_wait)
    monkeypatch.setattr(setup.usb, 'verify_reinitialize', lambda *a, **kw: NODE)
    monkeypatch.setattr(setup.usb, 'provision', original_wifi)
    monkeypatch.setattr(setup.usb, 'wait_for_registration_status', original_mqtt)
    monkeypatch.setattr(setup.secrets, 'token_hex', lambda *a: pytest.fail('retry rotated PoP'))
    reinitialize(reset_package, tmp_path)
    saved = setup.read_credential(tmp_path/'store/nodes', NODE)
    assert saved['provisioning_secret'] == pending['provisioning_secret']
    assert 'setup_state' not in saved


def test_reinitialize_requires_confirmation_and_supported_firmware(package, reset_package, tmp_path, hardware, monkeypatch):
    with pytest.raises(ProvisioningError, match='confirmation'):
        reinitialize(reset_package, tmp_path, False)
    assert hardware == [] and not (tmp_path/'store').exists()
    files = setup.validate_package(reset_package)
    files['firmware.bin'] = b'old firmware without reset support'
    monkeypatch.setattr(setup, 'validate_package', lambda *a: files)
    monkeypatch.setattr(setup.usb, 'identify', lambda *a: pytest.fail('unsupported package accessed USB'))
    with pytest.raises(ProvisioningError, match='reinitialize_firmware_required'):
        reinitialize(reset_package, tmp_path)
    assert hardware == [] and not (tmp_path/'store').exists()


def test_reinitialize_gateway_wifi_failure_precedes_destruction(reset_package, tmp_path, hardware, monkeypatch):
    credential = saved_credential(tmp_path)
    original = credential.read_bytes()
    def fail(): raise ProvisioningError('gateway_credential_unavailable')
    monkeypatch.setattr(setup.usb, 'read_gateway_wifi', fail)
    with pytest.raises(ProvisioningError, match='gateway_credential_unavailable'):
        reinitialize(reset_package, tmp_path)
    assert hardware == [] and credential.read_bytes() == original


@pytest.mark.parametrize('boundary', [1, 2, 3, 4])
def test_reinitialize_identity_change_stops_further_writes(reset_package, tmp_path, hardware, monkeypatch, boundary):
    calls = 0
    def mac(*a, **kw):
        nonlocal calls
        calls += 1
        return MAC if calls <= boundary else '02:00:00:00:00:02'
    monkeypatch.setattr(setup, 'read_mac', mac)
    with pytest.raises(ProvisioningError, match='identity_changed'):
        reinitialize(reset_package, tmp_path)
    assert len(hardware) == max(0, boundary-2)


def test_inventory_distinguishes_missing_present_and_partial_credential(tmp_path, hardware, monkeypatch):
    store = tmp_path/'store/nodes'
    monkeypatch.setattr(setup, 'STORE', store)
    assert setup.setup_candidates()[0]['credential_state'] == 'missing'
    credential = saved_credential(tmp_path)
    assert setup.setup_candidates()[0]['credential_state'] == 'present'
    saved = setup.read_credential(store, NODE)
    saved['setup_state'] = 'reinitialize_pending'
    setup.save_reinitialize_credential(store, saved)
    assert setup.setup_candidates()[0]['credential_state'] == 'reinitialize_pending'
    credential.write_text('{}')
    assert setup.setup_candidates()[0]['credential_state'] == 'invalid'


@pytest.mark.parametrize('state,logical_id', [('registered', 'old-sen66'), ('provisioned', 'old-sen66'), ('provisioned', None)])
def test_reinitialize_completion_requires_provisioned_without_old_id(monkeypatch, state, logical_id):
    class Process:
        returncode = 0
        def communicate(self, **kwargs):
            return json.dumps(dict(node_id=NODE, registration_state=state, logical_id=logical_id)), ''
    monkeypatch.setattr(setup.subprocess, 'Popen', lambda *a, **kw: Process())
    if state == 'provisioned' and logical_id is None:
        setup.usb.wait_for_registration_status(NODE, expected_state='provisioned')
    else:
        with pytest.raises(ProvisioningError, match='mqtt_registration_timeout'):
            setup.usb.wait_for_registration_status(NODE, expected_state='provisioned')


def test_reinitialize_credential_persistence_failure_never_writes_node(reset_package, tmp_path, hardware, monkeypatch):
    credential = saved_credential(tmp_path)
    before = credential.read_bytes()
    def fail(*a): raise OSError('simulated storage failure')
    monkeypatch.setattr(setup.os, 'replace', fail)
    with pytest.raises(OSError):
        reinitialize(reset_package, tmp_path)
    assert hardware == []
    assert credential.read_bytes() == before
    assert list(credential.parent.iterdir()) == [credential]
