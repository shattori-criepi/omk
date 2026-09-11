"""Bounded USB Serial/JTAG provisioning for known OMK Nodes.

This module deliberately accepts no shell command, Wi-Fi profile, broker, or
arbitrary serial path from its callers.  Secrets remain in this host process.
"""
from __future__ import annotations

import glob
import json
import os
import select
import subprocess
import termios
import time
from collections.abc import Callable

PROTOCOL_VERSION = 2
DEFAULT_PROFILE = "omk-ap"
DEFAULT_MQTT_BROKER = "127.0.0.1"
# AtomS3 Lite exposes its supported USB provisioning interface as USB CDC ACM
# (and, normally, as an Espressif /dev/serial/by-id symlink).  Do not probe
# every ttyUSB device: gateways can have modems and FTDI adapters for which an
# identify timeout is both irrelevant and expensive.
DEFAULT_DEVICE_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyACM*")
BY_ID_PREFIX = "/dev/serial/by-id/"
SERIAL_SETTLE_SECONDS = 0.5
INPUT_DISCARD_TIMEOUT_SECONDS = 0.05
INPUT_DISCARD_MAX_BYTES = 64 * 1024
IDENTIFY_ATTEMPTS = 3
IDENTIFY_RETRY_SECONDS = 0.1
IDENTIFY_ATTEMPT_TIMEOUT_SECONDS = 1.25


class ProvisioningError(RuntimeError):
    """A safe, stable provisioning failure code for the management API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def valid_node_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 12 and all(c in "0123456789abcdef" for c in value)


def is_protocol_response(message: object) -> bool:
    return isinstance(message, dict) and message.get("protocol_version") == PROTOCOL_VERSION and isinstance(message.get("status"), str) and valid_node_id(message.get("node_id"))


def _is_usb_response(message: object) -> bool:
    """Recognize legacy replies for inventory/errors, never authorize writes."""
    return (isinstance(message, dict) and type(message.get("protocol_version")) is int
            and message["protocol_version"] in {1, PROTOCOL_VERSION}
            and isinstance(message.get("status"), str) and valid_node_id(message.get("node_id")))


def profile_value(profile: str, field: str) -> str:
    result = subprocess.run(["nmcli", "-g", field, "connection", "show", profile], check=True, capture_output=True, text=True)
    return result.stdout.splitlines()[0] if result.stdout.splitlines() else ""


def read_profile_psk(profile: str) -> str:
    command: list[str] = [] if os.geteuid() == 0 else ["sudo", "--"]
    command += ["nmcli", "--show-secrets", "-g", "802-11-wireless-security.psk", "connection", "show", profile]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProvisioningError("gateway_credential_unavailable") from error
    return result.stdout.splitlines()[0] if result.stdout.splitlines() else ""


def read_gateway_wifi(profile: str = DEFAULT_PROFILE) -> tuple[str, str]:
    try:
        ssid = profile_value(profile, "802-11-wireless.ssid")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProvisioningError("gateway_credential_unavailable") from error
    password = read_profile_psk(profile)
    if not ssid or not password:
        raise ProvisioningError("gateway_credential_unavailable")
    return ssid, password


def candidate_devices() -> list[str]:
    devices: list[str] = []
    for pattern in DEFAULT_DEVICE_GLOBS:
        for device in glob.glob(pattern):
            if device not in devices and os.path.exists(device):
                devices.append(device)
    return devices


def canonical_device(device: str) -> str:
    """Compare only resolved paths that already came from our fixed allowlist."""
    return os.path.realpath(device)


def same_physical_device(first: str, second: str) -> bool:
    return canonical_device(first) == canonical_device(second)


def device_priority(device: str) -> tuple[int, str]:
    """Choose a stable, supported representative for a physical USB device."""
    if device.startswith(BY_ID_PREFIX):
        # AtomS3 Lite's USB JTAG/Serial Debug Unit must win over generic
        # symlinks and its ttyACM alias.
        if "espressif" in os.path.basename(device).lower():
            return (0, device)
        return (1, device)
    if device.startswith("/dev/ttyACM"):
        return (2, device)
    return (3, device)


def is_supported_device_group(aliases: list[str]) -> bool:
    """Accept AtomS3 Lite paths without opening unrelated serial hardware."""
    return any(
        device.startswith("/dev/ttyACM")
        or (device.startswith(BY_ID_PREFIX) and "espressif" in os.path.basename(device).lower())
        for device in aliases
    )


def physical_usb_devices() -> list[tuple[str, str]]:
    """Return one preferred allowlisted path per physical USB serial device."""
    aliases: dict[str, list[str]] = {}
    for device in candidate_devices():
        aliases.setdefault(canonical_device(device), []).append(device)
    groups = [
        (min(group, key=device_priority), canonical)
        for canonical, group in aliases.items()
        if is_supported_device_group(group)
    ]
    return sorted(groups, key=lambda group: device_priority(group[0]))


def _write_until(fd: int, data: bytes, deadline: float) -> None:
    """Queue the entire request on a nonblocking fd within the shared deadline."""
    pending = memoryview(data)
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("OMK USB provisioning write timeout")
        _, writable, _ = select.select([], [fd], [], remaining)
        if not writable:
            continue
        if time.monotonic() >= deadline:
            raise TimeoutError("OMK USB provisioning write timeout")
        try:
            written = os.write(fd, pending)
        except BlockingIOError:
            continue
        if written == 0:
            raise OSError("OMK USB provisioning write made no progress")
        pending = pending[written:]


def _discard_pending_input(fd: int, deadline: float) -> None:
    """Discard currently queued input without a potentially blocking TTY flush.

    The fd must be nonblocking. Fail closed if a noisy device exhausts either
    budget: sending a request with stale replies still queued would be unsafe.
    """
    deadline = min(deadline, time.monotonic() + INPUT_DISCARD_TIMEOUT_SECONDS)
    discarded = 0
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0)
        if not readable:
            return
        if time.monotonic() >= deadline:
            break
        try:
            chunk = os.read(fd, min(4096, INPUT_DISCARD_MAX_BYTES - discarded))
        except BlockingIOError:
            return
        if not chunk:
            raise OSError("OMK USB provisioning device disconnected")
        discarded += len(chunk)
        if discarded >= INPUT_DISCARD_MAX_BYTES:
            break
    raise TimeoutError("OMK USB provisioning input discard limit reached")


class SerialJson:
    def __init__(self, device: str, *, deadline: float | None = None) -> None:
        self.device = device
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attributes = termios.tcgetattr(self.fd)
            attributes[0] = attributes[1] = attributes[3] = 0
            attributes[2] |= termios.CLOCAL | termios.CREAD
            attributes[4] = attributes[5] = termios.B115200
            attributes[6][termios.VMIN] = attributes[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
            settle = SERIAL_SETTLE_SECONDS
            if deadline is not None:
                settle = min(settle, max(0, deadline - time.monotonic()))
            time.sleep(settle)
            _discard_pending_input(self.fd, deadline if deadline is not None
                                   else time.monotonic() + INPUT_DISCARD_TIMEOUT_SECONDS)
        except BaseException:
            os.close(self.fd)
            raise
        self._buffer = b""

    def close(self) -> None:
        os.close(self.fd)

    def request(self, request: dict[str, object], timeout: float, matches: Callable[[dict[str, object]], bool]) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        data = (json.dumps(request, separators=(",", ":")) + "\n").encode()
        # Also discard replies left by an earlier request on this connection.
        self._buffer = b""
        _discard_pending_input(self.fd, deadline)
        _write_until(self.fd, data, deadline)
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not readable:
                continue
            try:
                chunk = os.read(self.fd, 512)
            except BlockingIOError:
                continue
            if not chunk:
                raise OSError("OMK USB provisioning device disconnected")
            self._buffer += chunk
            while b"\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\n", 1)
                try:
                    response = json.loads(raw.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(response, dict) and matches(response):
                    return response
        raise TimeoutError(f"No OMK USB provisioning response from {self.device}")


def identify(device: str, timeout: float = 10) -> dict[str, object] | None:
    deadline = time.monotonic() + timeout
    for attempt in range(IDENTIFY_ATTEMPTS):
        if time.monotonic() >= deadline:
            return None
        serial = None
        try:
            serial = SerialJson(device, deadline=deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            # Read-only v1 discovery also counts old Nodes; they must not be
            # silently excluded from the multiple-Node safety check.
            response = serial.request({"command": "identify", "protocol_version": 1},
                                      min(IDENTIFY_ATTEMPT_TIMEOUT_SECONDS, remaining),
                                      lambda message: _is_usb_response(message) and message["status"] == "ok")
            return response
        except (OSError, termios.error, TimeoutError):
            if attempt + 1 == IDENTIFY_ATTEMPTS or time.monotonic() >= deadline:
                return None
        finally:
            if serial is not None:
                serial.close()
        time.sleep(min(IDENTIFY_RETRY_SECONDS, max(0, deadline - time.monotonic())))
    return None


def usb_candidates(timeout: float = 3) -> list[dict[str, str | bool]]:
    # Identify exactly once for each physical USB device.  The representative
    # is the stable by-id path when available, so aliases never cause a second
    # serial open or an unstable path in the UI.
    nodes: dict[str, dict[str, str | bool]] = {}
    duplicate_identity = False
    for device, _canonical in physical_usb_devices():
        response = identify(device, timeout)
        if response is not None:
            node_id = str(response["node_id"])
            if node_id in nodes:
                # Aliases were already collapsed by realpath. Equal application
                # IDs on distinct ports are ambiguous, never interchangeable.
                duplicate_identity = True
            nodes[node_id] = {"device": device, "node_id": node_id,
                              "wifi_configured": response.get("wifi_configured") is True}
    if duplicate_identity:
        raise ProvisioningError("ambiguous_node_identity")
    return list(nodes.values())


def _change_wifi(device: str, expected_node_id: str, command: str,
                 fields: dict[str, object], timeout: float) -> str:
    """Identify and mutate on one open connection; never send secrets first."""
    if not valid_node_id(expected_node_id):
        raise ProvisioningError("node_identity_changed")
    serial = SerialJson(device)
    try:
        identity = serial.request(
            {"command": "identify", "protocol_version": PROTOCOL_VERSION}, timeout,
            lambda message: _is_usb_response(message) and message["status"] in {"ok", "invalid_request"})
        if identity["protocol_version"] != PROTOCOL_VERSION or identity["status"] != "ok":
            raise ProvisioningError("unsupported_usb_protocol")
        if identity["node_id"] != expected_node_id:
            raise ProvisioningError("node_identity_changed")
        response = serial.request(
            {"command": command, "protocol_version": PROTOCOL_VERSION,
             "expected_node_id": expected_node_id, **fields}, timeout,
            lambda message: is_protocol_response(message) and message["status"] in {
                "accepted", "invalid_request", "busy", "storage_error",
                "restart_error", "node_identity_changed"})
        if response["node_id"] != expected_node_id:
            raise ProvisioningError("node_identity_changed")
        if response["status"] != "accepted":
            raise ProvisioningError(str(response["status"]))
        return expected_node_id
    finally:
        serial.close()


def provision(device: str, ssid: str, password: str, timeout: float = 10,
              *, expected_node_id: str) -> str:
    return _change_wifi(device, expected_node_id, "set_wifi",
                        {"ssid": ssid, "password": password}, timeout)


def clear_wifi(device: str, timeout: float = 10, *, expected_node_id: str) -> str:
    """Development-only command; uses the same pre-mutation identity guard."""
    return _change_wifi(device, expected_node_id, "clear_wifi", {}, timeout)


def wait_for_rebooted_identify(device: str, node_id: str, timeout: float = 60) -> None:
    """Wait past the scheduled reboot, then prove the same USB Node returned."""
    deadline = time.monotonic() + timeout
    time.sleep(1)
    while time.monotonic() < deadline:
        response = identify(device, min(3, max(0.1, deadline - time.monotonic())))
        if response is not None and response.get("node_id") == node_id:
            return
        time.sleep(0.5)
    raise ProvisioningError("clear_wifi_reboot_timeout")


def find_usb_node_by_id(node_id: str, timeout: float = 3) -> tuple[str, str]:
    if not valid_node_id(node_id):
        raise RuntimeError("Invalid Node ID")
    matches = [node for node in usb_candidates(timeout) if node["node_id"] == node_id]
    if len(matches) != 1:
        raise RuntimeError("Requested OMK Node was not uniquely identified on USB")
    return str(matches[0]["device"]), node_id


def wait_for_registration_status(node_id: str, broker: str = DEFAULT_MQTT_BROKER, timeout: float = 60, *, fresh: bool = False) -> None:
    process = subprocess.Popen(["mosquitto_sub", *(["-R"] if fresh else []), "-h", broker, "-C", "1", "-W", str(int(timeout)), "-t", f"omk/node/{node_id}/registration/status"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        message, _ = process.communicate(timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        process.kill(); process.communicate()
        raise ProvisioningError("mqtt_registration_timeout") from None
    if process.returncode != 0:
        raise ProvisioningError("mqtt_registration_timeout")
    try:
        status = json.loads(message)
    except json.JSONDecodeError:
        raise ProvisioningError("mqtt_registration_timeout") from None
    if status.get("node_id") != node_id or status.get("registration_state") not in {"provisioned", "registered"}:
        raise ProvisioningError("mqtt_registration_timeout")


def provision_selected_node(device: str, node_id: str) -> None:
    # Re-discover and identify immediately before sending credentials. This is
    # the allowlist that prevents a request from selecting an arbitrary path.
    allowed_devices = candidate_devices()
    matching = next((item for item in usb_candidates() if item["node_id"] == node_id and
                     same_physical_device(device, item["device"])), None)
    if device not in allowed_devices or matching is None:
        raise ProvisioningError("node_not_available")
    ssid, password = read_gateway_wifi()
    try:
        provisioned_node_id = provision(matching["device"], ssid, password, expected_node_id=node_id)
    except ProvisioningError:
        raise
    except (OSError, termios.error, TimeoutError):
        raise ProvisioningError("set_wifi_failed") from None
    if provisioned_node_id != node_id:
        raise ProvisioningError("node_identity_changed")
    wait_for_registration_status(node_id)
