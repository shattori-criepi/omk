#!/usr/bin/env python3
"""Provision an OMK Node over USB Serial/JTAG using Gateway credentials."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/system-manager/src"))
from omk_system_manager import node_provisioning as _core
from omk_system_manager.node_provisioning import *  # noqa: F403


def provision(device: str, ssid: str, password: str, timeout: float = 10) -> str:
    """Compatibility shim for callers importing this historical CLI module."""
    _core.SerialJson = SerialJson  # type: ignore[name-defined] # noqa: F405
    return _core.provision(device, ssid, password, timeout)


def clear_wifi(device: str, timeout: float = 10) -> str:
    _core.SerialJson = SerialJson  # type: ignore[name-defined] # noqa: F405
    return _core.clear_wifi(device, timeout)


def find_node(device: str | None, timeout: float) -> tuple[str, str]:
    candidates = [device] if device else candidate_devices()  # noqa: F405
    for candidate in candidates:
        response = identify(candidate, timeout)  # noqa: F405
        if response is not None:
            return candidate, str(response["node_id"])
    raise RuntimeError("No OMK Node responded on USB serial devices")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)  # noqa: F405
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--status-timeout", type=float, default=60)
    parser.add_argument("--mqtt-broker", default=DEFAULT_MQTT_BROKER)  # noqa: F405
    parser.add_argument("--clear-wifi", action="store_true", help="development only: erase this Node's Gateway Wi-Fi credential")
    parser.add_argument("--node-id", help="development clear target; selects one identified USB Node")
    args = parser.parse_args()
    if args.timeout <= 0 or args.status_timeout <= 0:
        parser.error("--timeout and --status-timeout must be positive")
    if args.clear_wifi and bool(args.device) == bool(args.node_id):
        parser.error("--clear-wifi requires exactly one of --device or --node-id")
    if args.node_id and not args.clear_wifi:
        parser.error("--node-id is only available with --clear-wifi")
    if args.clear_wifi and args.node_id:
        device, node_id = find_usb_node_by_id(args.node_id, args.timeout)  # noqa: F405
    else:
        device, node_id = find_node(args.device, args.timeout)
    if args.clear_wifi:
        if clear_wifi(device, args.timeout) != node_id:
            raise RuntimeError("Node identity changed while clearing Wi-Fi")
        wait_for_rebooted_identify(device, node_id, args.status_timeout)  # noqa: F405
        print(f"USB Wi-Fi credential cleared for node_id={node_id}.")
        return 0
    ssid, password = read_gateway_wifi(args.profile)  # noqa: F405
    if provision(device, ssid, password, args.timeout) != node_id:  # noqa: F405
        raise RuntimeError("Node identity changed during USB provisioning")
    wait_for_registration_status(node_id, args.mqtt_broker, args.status_timeout)  # noqa: F405
    print(f"USB provisioning confirmed for node_id={node_id}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print(f"USB provisioning failed: {error}", file=sys.stderr)
        raise SystemExit(1)
