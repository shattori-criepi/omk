#!/usr/bin/env python3
"""Provision an OMK Node over its normal USB Serial/JTAG connection.

The Wi-Fi password is read from the Gateway's NetworkManager profile only. It
is never accepted as a command-line argument, printed, or written to disk.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import select
import subprocess
import sys
import termios
import time
from collections.abc import Callable

PROTOCOL_VERSION = 1
DEFAULT_PROFILE = "omk-ap"
DEFAULT_MQTT_BROKER = "192.168.50.1"
DEFAULT_DEVICE_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*")
SERIAL_SETTLE_SECONDS = 0.5


def valid_node_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 12 and
            all(character in "0123456789abcdef" for character in value))


def is_protocol_response(message: object) -> bool:
    return (isinstance(message, dict) and message.get("protocol_version") == PROTOCOL_VERSION and
            isinstance(message.get("status"), str) and valid_node_id(message.get("node_id")))


def profile_value(profile: str, field: str) -> str:
    command = ["nmcli"]
    command.extend(["-g", field, "connection", "show", profile])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    values = result.stdout.splitlines()
    return values[0] if values else ""


def read_profile_psk(profile: str) -> str:
    """Read only the protected NetworkManager PSK with elevated privilege.

    The rest of this process, including USB serial access, deliberately stays
    unprivileged. stdout is captured in memory; neither the PSK nor command
    output can reach a terminal, log, argv, or temporary file.
    """
    command: list[str] = [] if os.geteuid() == 0 else ["sudo", "--"]
    command.extend(["nmcli", "--show-secrets", "-g",
                    "802-11-wireless-security.psk", "connection", "show", profile])
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Unable to read the OMK AP credential") from error
    values = result.stdout.splitlines()
    return values[0] if values else ""


def read_gateway_wifi(profile: str) -> tuple[str, str]:
    ssid = profile_value(profile, "802-11-wireless.ssid")
    password = read_profile_psk(profile)
    if not ssid or not password:
        raise RuntimeError("OMK AP credential is unavailable from NetworkManager")
    return ssid, password


def candidate_devices() -> list[str]:
    devices: list[str] = []
    for pattern in DEFAULT_DEVICE_GLOBS:
        for device in glob.glob(pattern):
            if device not in devices and os.path.exists(device):
                devices.append(device)
    return devices


class SerialJson:
    def __init__(self, device: str) -> None:
        self.device = device
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attributes = termios.tcgetattr(self.fd)
        attributes[0] = 0
        attributes[1] = 0
        attributes[2] |= termios.CLOCAL | termios.CREAD
        attributes[3] = 0
        # Match the known-good pyserial setup. CDC ACM normally ignores the
        # nominal baud rate, but setting it avoids relying on prior tty state.
        attributes[4] = termios.B115200
        attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = 0
        attributes[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        # ESP32-S3 Serial/JTAG may emit boot/log data immediately after open.
        # Let the host endpoint settle, then discard only pre-request bytes.
        time.sleep(SERIAL_SETTLE_SECONDS)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self._buffer = b""

    def close(self) -> None:
        os.close(self.fd)

    def request(self, request: dict[str, object], timeout: float,
                matches: Callable[[dict[str, object]], bool]) -> dict[str, object]:
        payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        os.write(self.fd, payload)
        termios.tcdrain(self.fd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.fd], [], [], deadline - time.monotonic())
            if not readable:
                continue
            data = os.read(self.fd, 512)
            if not data:
                continue
            self._buffer += data
            while b"\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\n", 1)
                try:
                    response = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue  # ESP logs share the console; only JSON is protocol data.
                # Console logs and unrelated JSON may share this stream. Keep
                # reading until this request's expected protocol response.
                if isinstance(response, dict) and matches(response):
                    return response
        raise TimeoutError(f"No OMK USB provisioning response from {self.device}")


def identify(device: str, timeout: float) -> dict[str, object] | None:
    serial = None
    try:
        serial = SerialJson(device)
        response = serial.request(
            {"command": "identify", "protocol_version": PROTOCOL_VERSION}, timeout,
            lambda message: is_protocol_response(message) and message["status"] == "ok")
        return response
    except (OSError, termios.error, TimeoutError):
        return None
    finally:
        if serial is not None:
            serial.close()
    return None


def find_node(device: str | None, timeout: float) -> tuple[str, str]:
    candidates = [device] if device else candidate_devices()
    for candidate in candidates:
        response = identify(candidate, timeout)
        if response is not None:
            return candidate, str(response["node_id"])
    raise RuntimeError("No OMK Node responded on USB serial devices")


def provision(device: str, ssid: str, password: str, timeout: float) -> str:
    serial = SerialJson(device)
    try:
        response = serial.request({"command": "set_wifi", "protocol_version": PROTOCOL_VERSION,
                                   "ssid": ssid, "password": password}, timeout,
                                  lambda message: (is_protocol_response(message) and
                                                   message["status"] in {"accepted", "invalid_request", "busy",
                                                                         "storage_error", "restart_error"}))
    finally:
        serial.close()
    if response.get("status") != "accepted" or not isinstance(response.get("node_id"), str):
        raise RuntimeError(f"Node rejected Wi-Fi configuration ({response.get('status', 'unknown')})")
    return str(response["node_id"])


def wait_for_registration_status(node_id: str, broker: str, timeout: float) -> None:
    """Confirm the post-reboot normal Wi-Fi/MQTT path using its retained status."""
    topic = f"omk/node/{node_id}/registration/status"
    process = subprocess.Popen(["mosquitto_sub", "-h", broker, "-C", "1", "-W", str(int(timeout)),
                                "-t", topic], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        message, _ = process.communicate(timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("Timed out waiting for Node MQTT registration status") from None
    if process.returncode != 0:
        raise RuntimeError("Node MQTT registration status was not received")
    try:
        status = json.loads(message)
    except json.JSONDecodeError:
        raise RuntimeError("Node MQTT registration status was invalid") from None
    if status.get("node_id") != node_id or status.get("registration_state") not in {"provisioned", "registered"}:
        raise RuntimeError("Node MQTT registration status did not confirm provisioning")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="optional USB serial device; otherwise all candidates are identified")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="NetworkManager Wi-Fi profile (default: omk-ap)")
    parser.add_argument("--timeout", type=float, default=10,
                        help="per-command response timeout in seconds (default: 10)")
    parser.add_argument("--status-timeout", type=float, default=60,
                        help="post-reboot MQTT confirmation timeout in seconds")
    parser.add_argument("--mqtt-broker", default=DEFAULT_MQTT_BROKER,
                        help="MQTT broker used for registration confirmation")
    args = parser.parse_args()
    if args.timeout <= 0 or args.status_timeout <= 0:
        parser.error("--timeout and --status-timeout must be positive")
    device, identified_node_id = find_node(args.device, args.timeout)
    ssid, password = read_gateway_wifi(args.profile)
    node_id = provision(device, ssid, password, args.timeout)
    if node_id != identified_node_id:
        raise RuntimeError("Node identity changed during USB provisioning")
    wait_for_registration_status(node_id, args.mqtt_broker, args.status_timeout)
    print(f"USB provisioning confirmed for node_id={node_id}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"USB provisioning failed: {error}", file=sys.stderr)
        raise SystemExit(1)
