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

PROTOCOL_VERSION = 1
DEFAULT_PROFILE = "omk-ap"
DEFAULT_MQTT_BROKER = "192.168.50.1"
DEFAULT_DEVICE_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*")
SERIAL_SETTLE_SECONDS = 0.5
IDENTIFY_ATTEMPTS = 3
IDENTIFY_RETRY_SECONDS = 0.1


class ProvisioningError(RuntimeError):
    """A safe, stable provisioning failure code for the management API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def valid_node_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 12 and all(c in "0123456789abcdef" for c in value)


def is_protocol_response(message: object) -> bool:
    return isinstance(message, dict) and message.get("protocol_version") == PROTOCOL_VERSION and isinstance(message.get("status"), str) and valid_node_id(message.get("node_id"))


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


class SerialJson:
    def __init__(self, device: str) -> None:
        self.device = device
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attributes = termios.tcgetattr(self.fd)
        attributes[0] = attributes[1] = attributes[3] = 0
        attributes[2] |= termios.CLOCAL | termios.CREAD
        attributes[4] = attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = attributes[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        time.sleep(SERIAL_SETTLE_SECONDS)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self._buffer = b""

    def close(self) -> None:
        os.close(self.fd)

    def request(self, request: dict[str, object], timeout: float, matches: Callable[[dict[str, object]], bool]) -> dict[str, object]:
        os.write(self.fd, (json.dumps(request, separators=(",", ":")) + "\n").encode())
        termios.tcdrain(self.fd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not readable:
                continue
            self._buffer += os.read(self.fd, 512)
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
        serial = None
        try:
            serial = SerialJson(device)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            response = serial.request({"command": "identify", "protocol_version": PROTOCOL_VERSION},
                                      remaining / (IDENTIFY_ATTEMPTS - attempt),
                                      lambda message: is_protocol_response(message) and message["status"] == "ok")
            return response
        except (OSError, termios.error, TimeoutError):
            if attempt + 1 == IDENTIFY_ATTEMPTS or time.monotonic() >= deadline:
                return None
            time.sleep(min(IDENTIFY_RETRY_SECONDS, max(0, deadline - time.monotonic())))
        finally:
            if serial is not None:
                serial.close()
    return None


def usb_candidates(timeout: float = 3) -> list[dict[str, str]]:
    # candidate_devices() is ordered by stable /dev/serial/by-id paths first.
    # Keep the first identified path for each physical Node, so the UI never
    # renders its by-id symlink and ttyACM/ttyUSB alias as separate Nodes.
    devices = candidate_devices()
    aliases: dict[str, list[str]] = {}
    for device in devices:
        aliases.setdefault(canonical_device(device), []).append(device)
    nodes: dict[str, dict[str, str]] = {}
    for device in devices:
        response = identify(device, timeout)
        if response is not None:
            node_id = str(response["node_id"])
            # The first alias in a canonical group is /dev/serial/by-id when
            # present, even if its first identify response was missed.
            preferred = aliases[canonical_device(device)][0]
            nodes.setdefault(node_id, {"device": preferred, "node_id": node_id})
    return list(nodes.values())


def provision(device: str, ssid: str, password: str, timeout: float = 10) -> str:
    serial = SerialJson(device)
    try:
        response = serial.request({"command": "set_wifi", "protocol_version": PROTOCOL_VERSION, "ssid": ssid, "password": password}, timeout,
                                  lambda message: is_protocol_response(message) and message["status"] in {"accepted", "invalid_request", "busy", "storage_error", "restart_error"})
    finally:
        serial.close()
    if response.get("status") != "accepted":
        raise ProvisioningError("set_wifi_failed")
    return str(response["node_id"])


def clear_wifi(device: str, timeout: float = 10) -> str:
    """Development-only command; it never reads or handles the AP PSK."""
    serial = SerialJson(device)
    try:
        response = serial.request({"command": "clear_wifi", "protocol_version": PROTOCOL_VERSION}, timeout,
                                  lambda message: is_protocol_response(message) and message["status"] in {"accepted", "busy", "storage_error", "restart_error"})
    finally:
        serial.close()
    if response.get("status") != "accepted":
        raise ProvisioningError("clear_wifi_failed")
    return str(response["node_id"])


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


def wait_for_registration_status(node_id: str, broker: str = DEFAULT_MQTT_BROKER, timeout: float = 60) -> None:
    process = subprocess.Popen(["mosquitto_sub", "-h", broker, "-C", "1", "-W", str(int(timeout)), "-t", f"omk/node/{node_id}/registration/status"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
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
        provisioned_node_id = provision(matching["device"], ssid, password)
    except ProvisioningError:
        raise
    except (OSError, termios.error, TimeoutError):
        raise ProvisioningError("set_wifi_failed") from None
    if provisioned_node_id != node_id:
        raise ProvisioningError("node_identity_changed")
    wait_for_registration_status(node_id)
