"""Fixed AtomS3 Lite package and identity-guarded USB setup. No root helper."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time

from . import node_provisioning as usb
from .node_provisioning import ProvisioningError

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / 'firmware/esp32/omk-node/prebuilt/atom-s3-lite'
STORE = ROOT / 'data/provisioning/nodes'
# Erase sectors cannot extend into NVS (0x9000) or factory secret (0xf000).
SEGMENTS = (('bootloader.bin', '0x00000000', 0x8000),
            ('partitions.bin', '0x00008000', 0x1000),
            ('firmware.bin', '0x00010000', 0x200000))
MQTT_REGISTRATION_TIMEOUT_SECONDS = 180


def regular_bytes(path: Path, maximum: int) -> bytes:
    # Reject symlinks throughout the path, including the package directory.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ProvisioningError('firmware_package_invalid')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise ProvisioningError('firmware_package_invalid')
        return stream.read(maximum + 1)


def validate_package(package: Path = PACKAGE) -> dict[str, bytes]:
    """Return validated bytes; writes use a private snapshot, never original paths."""
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate key')
                result[key] = value
            return result
        manifest = json.loads(regular_bytes(package / 'manifest.json', 16384), object_pairs_hook=unique)
        if (type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['target'] != 'atom-s3-lite' or manifest['chip'] != 'esp32s3'
                or not re.fullmatch('[0-9a-f]{40}', manifest['source_commit'])
                or not isinstance(manifest['segments'], list) or len(manifest['segments']) != 3):
            raise ValueError('manifest')
        files = {}
        for segment, (filename, offset, maximum) in zip(manifest['segments'], SEGMENTS):
            if (set(segment) != {'filename', 'offset', 'size', 'sha256'}
                    or segment['filename'] != filename or segment['offset'] != offset
                    or type(segment['size']) is not int):
                raise ValueError('segment')
            content = regular_bytes(package / filename, maximum)
            if len(content) != segment['size'] or hashlib.sha256(content).hexdigest() != segment['sha256']:
                raise ValueError('hash/size')
            files[filename] = content
        return files
    except FileNotFoundError:
        raise ProvisioningError('firmware_package_missing') from None
    except (OSError, ValueError, TypeError, KeyError):
        raise ProvisioningError('firmware_package_invalid') from None


def node_id_from_mac(mac: str) -> str:
    value = 14695981039346656037
    for byte in bytes.fromhex(mac.replace(':', '')):
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return f'{value & ((1 << 48) - 1):012x}'


def esptool(device: str, arguments: list[str], code: str) -> str:
    try:
        result = subprocess.run([sys.executable, '-m', 'esptool', '--port', device,
                                 *arguments], capture_output=True, text=True, timeout=15 if arguments[-1] == 'read_mac' else 180, check=True)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        # Never log subprocess exceptions/output (MAC and device-specific data).
        raise ProvisioningError(code) from None


def read_mac(device: str, *, stay_in_bootloader: bool = False) -> str:
    arguments = ['--after', 'no_reset', 'read_mac'] if stay_in_bootloader else ['read_mac']
    output = esptool(device, arguments, 'device_inspection_failed')
    chips = [line.strip() for line in output.splitlines() if 'Chip is' in line]
    if not chips or any(not re.fullmatch(r'Chip is ESP32-S3(?: .+)?', line) for line in chips):
        raise ProvisioningError('unsupported_chip')
    reports = [line.strip() for line in output.splitlines() if 'MAC:' in line]
    if not reports or any(not re.fullmatch(r'MAC: (?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line) for line in reports):
        raise ProvisioningError('ambiguous_mac')
    macs = {line[5:].lower() for line in reports}
    if len(macs) != 1:
        raise ProvisioningError('ambiguous_mac')
    return macs.pop()


def allowed(device: str) -> bool:
    # Even an Espressif-named by-id alias must not resolve to ttyUSB.
    return any(device == path and re.fullmatch(r'/dev/ttyACM[0-9]+', canonical)
               for path, canonical in usb.physical_usb_devices())


def selected_device(device: str) -> str:
    # Public inventory uses ttyACM names: by-id USB serial strings can contain
    # the raw MAC. Resolve once to the stable internal path, then keep it for
    # every subsequent identity check and write (including re-enumeration).
    for path, canonical in usb.physical_usb_devices():
        if device in {path, canonical} and re.fullmatch(r'/dev/ttyACM[0-9]+', canonical):
            return path
    raise ProvisioningError('node_not_available')


def read_credential(store: Path, node_id: str) -> dict | None:
    path = store / f'{node_id}.json'
    if not os.path.lexists(path):
        return None
    try:
        value = json.loads(regular_bytes(path, 4096))
        if (not isinstance(value, dict) or value.get('node_id') != node_id or
                value.get('board') != 'atom-s3-lite' or
                not isinstance(value.get('provisioning_secret'), str) or
                not re.fullmatch('[0-9a-fA-F]{64}', value['provisioning_secret'])):
            raise ValueError('credential')
        return value
    except (OSError, ValueError, ProvisioningError):
        raise ProvisioningError('credential_store_invalid') from None


def credential_state(store: Path, node_id: str) -> str:
    try:
        value = read_credential(store, node_id)
    except ProvisioningError:
        return 'invalid'
    if value is None:
        return 'missing'
    return 'reinitialize_pending' if value.get('setup_state') == 'reinitialize_pending' else 'present'


def save_reinitialize_credential(store: Path, value: dict) -> None:
    """Durable publication before hardware writes; pending retries reuse this PoP."""
    target = store / f"{value['node_id']}.json"
    if any(path.is_symlink() for path in (target, store, *store.parents)):
        raise ProvisioningError('credential_store_invalid')
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    store.chmod(0o700)
    store.parent.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=store, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_candidates(timeout: float = 3) -> list[dict]:
    result = []
    seen = set()
    for device, canonical in usb.physical_usb_devices():
        if not re.fullmatch(r'/dev/ttyACM[0-9]+', canonical):
            continue
        identity = usb.identify(device, timeout)
        if identity:
            node_id = str(identity['node_id'])
            kind = 'omk_node'
        else:
            try:
                node_id = node_id_from_mac(read_mac(device))
            except ProvisioningError:
                continue
            kind = 'recovery_required' if os.path.lexists(STORE / f'{node_id}.json') else 'unconfirmed_esp32s3'
        if node_id in seen:
            raise ProvisioningError('ambiguous_node_identity')
        seen.add(node_id)
        result.append(dict(device=canonical, node_id=node_id, kind=kind,
                           wifi_configured=bool(identity and identity.get('wifi_configured')),
                           credential_state=credential_state(STORE, node_id)))
    return result


def confirm_identity(device: str, expected_mac: str, *, stay_in_bootloader: bool = True) -> None:
    if not allowed(device):
        raise ProvisioningError('node_not_available')
    if read_mac(device, stay_in_bootloader=stay_in_bootloader) != expected_mac:
        raise ProvisioningError('node_identity_changed')


def wait_for_node(device: str, node_id: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    time.sleep(1)
    while time.monotonic() < deadline:
        if allowed(device):
            identity = usb.identify(device, timeout=3)
            if identity:
                if identity['node_id'] != node_id:
                    raise ProvisioningError('node_identity_changed')
                if identity['protocol_version'] != 2:
                    raise ProvisioningError('unsupported_usb_protocol')
                return
        time.sleep(0.5)
    raise ProvisioningError('node_reappearance_timeout')


def setup(device: str, node_id: str, confirmed: bool, stage, *, package: Path = PACKAGE,
          store: Path = STORE, reinitialize: bool = False) -> None:
    if reinitialize and confirmed is not True:
        raise ProvisioningError('reinitialize_confirmation_required')
    stage('validating_firmware')
    files = validate_package(package)  # Before any USB access, even identify.
    if reinitialize and b'verify_reinitialize\x00' not in files['firmware.bin']:
        raise ProvisioningError('reinitialize_firmware_required')
    # Fetch AP configuration before any destructive operation.
    new_wifi = usb.read_gateway_wifi() if reinitialize else None
    stage('checking_device')
    device = selected_device(device)
    if not usb.valid_node_id(node_id) or not allowed(device):
        raise ProvisioningError('node_not_available')
    identity = usb.identify(device)
    mac = read_mac(device, stay_in_bootloader=True)
    if node_id_from_mac(mac) != node_id or (identity and identity['node_id'] != node_id):
        raise ProvisioningError('node_identity_changed')
    credential = store / f'{node_id}.json'
    if not reinitialize and identity:
        state = credential_state(store, node_id)
        if state != 'present':
            raise ProvisioningError('reinitialize_required' if state == 'missing' else 'credential_store_invalid')
    elif not reinitialize:
        if os.path.lexists(credential):
            raise ProvisioningError('recovery_required')
        if confirmed is not True:
            raise ProvisioningError('atom_s3_lite_confirmation_required')
    with tempfile.TemporaryDirectory(prefix='omk-node-') as temporary:
        snapshot = Path(temporary)
        for filename, content in files.items():
            (snapshot / filename).write_bytes(content)
        if reinitialize:
            # Explicit consent permits replacement; a partial retry never rotates again.
            state = credential_state(store, node_id)
            saved = read_credential(store, node_id) if state == 'reinitialize_pending' else None
            if saved is None:
                saved = dict(node_id=node_id, provisioning_secret=secrets.token_hex(32),
                             board='atom-s3-lite', setup_state='reinitialize_pending')
            confirm_identity(device, mac)
            save_reinitialize_credential(store, saved)
            record = snapshot / 'factory.bin'
            record.write_bytes(b'OMKR' + bytes.fromhex(saved['provisioning_secret']))
            record.chmod(0o600)
            stage('reinitializing_node')
            confirm_identity(device, mac)
            esptool(device, ['--after', 'no_reset', '--chip', 'esp32s3', 'write_flash', '0xf000', str(record)], 'factory_write_failed')
        elif not identity:
            secret = secrets.token_bytes(32)
            record = snapshot / 'factory.bin'
            record.write_bytes(b'OMKP' + secret)
            record.chmod(0o600)
            stage('flashing_factory')
            # Keep the ROM/stub alive between inspection and writes so a reset
            # cannot race the next serial open (especially on blank devices).
            confirm_identity(device, mac)
            if any(path.is_symlink() for path in (store, *store.parents)):
                raise ProvisioningError('credential_store_invalid')
            store.mkdir(parents=True, exist_ok=True, mode=0o700)
            store.chmod(0o700)
            store.parent.chmod(0o700)
            # Exclusive creation: never overwrite a credential from an earlier attempt.
            fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(dict(node_id=node_id, provisioning_secret=secret.hex(), board='atom-s3-lite'), stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                directory_fd = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                # Recheck after persistence, too; remove credential if no write attempted.
                confirm_identity(device, mac)
            except Exception:
                credential.unlink()
                raise
            esptool(device, ['--after', 'no_reset', '--chip', 'esp32s3', 'write_flash', '0xf000', str(record)], 'factory_write_failed')
        stage('flashing_firmware')
        confirm_identity(device, mac)
        arguments = ['--chip', 'esp32s3', 'write_flash']
        for filename, offset, _ in SEGMENTS:
            arguments += [offset, str(snapshot / filename)]
        esptool(device, arguments, 'firmware_write_failed')
        stage('waiting_for_node')
        wait_for_node(device, node_id)
        # eFuse continuity after re-enumeration; read_mac reboots the device again.
        confirm_identity(device, mac, stay_in_bootloader=False)
        wait_for_node(device, node_id)
        if reinitialize:
            stage('verifying_reinitialize')
            usb.verify_reinitialize(device, saved['provisioning_secret'].lower(), expected_node_id=node_id)
        stage('configuring_wifi')
        ssid, password = new_wifi if new_wifi is not None else usb.read_gateway_wifi()
        try:
            usb.provision(device, ssid, password, expected_node_id=node_id)
        except ProvisioningError:
            raise
        except (OSError, TimeoutError):
            raise ProvisioningError('set_wifi_failed') from None
        stage('waiting_for_registration')
        usb.wait_for_registration_status(
            node_id, timeout=MQTT_REGISTRATION_TIMEOUT_SECONDS, fresh=True,
            **({'expected_state': 'provisioned'} if reinitialize else {})
        )
        if reinitialize:
            saved.pop('setup_state', None)
            save_reinitialize_credential(store, saved)


class SetupController:
    def __init__(self, operation_lock, serial_lock):
        self.operation_lock = operation_lock
        self.serial_lock = serial_lock
        self.lock = threading.Lock()
        self.state = {'stage': 'idle', 'node_id': None, 'error': None}
        self.worker = None

    def status(self):
        with self.lock:
            return dict(self.state)

    def stage(self, value):
        with self.lock:
            self.state['stage'] = value

    def start(self, device, node_id, confirmed, *, reinitialize=False):
        if not self.operation_lock.acquire(blocking=False):
            return False
        with self.lock:
            self.state = {'stage': 'validating_firmware', 'node_id': node_id, 'error': None}
        self.worker = threading.Thread(target=self.run, args=(device, node_id, confirmed, reinitialize), daemon=True)
        try:
            self.worker.start()
        except Exception:
            self.operation_lock.release()
            self.stage('failed')
            raise
        return True

    def run(self, device, node_id, confirmed, reinitialize=False):
        try:
            if not self.serial_lock.acquire(timeout=30):
                raise ProvisioningError('serial_busy')
            try:
                setup(device, node_id, confirmed, self.stage, **({"reinitialize": True} if reinitialize else {}))
            finally:
                self.serial_lock.release()
            self.stage('completed')
        except Exception as error:
            with self.lock:
                self.state['error'] = error.code if isinstance(error, ProvisioningError) else 'setup_failed'
                self.state['stage'] = 'failed'
        finally:
            self.operation_lock.release()
