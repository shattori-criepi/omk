#!/usr/bin/env python3
"""Provision an OMK Node over USB Serial/JTAG using Gateway credentials."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/system-manager/src"))
from omk_system_manager import node_provisioning as _core
from omk_system_manager.node_provisioning import *  # noqa: F403


def find_node(device: str | None, timeout: float) -> tuple[str, str]:
    if device:
        response = identify(device, timeout)  # noqa: F405
        if response is None:
            raise RuntimeError("No compatible OMK Node responded on the selected USB port (USB protocol v2 required)")
        return device, str(response["node_id"])
    nodes = usb_candidates(timeout)  # noqa: F405
    if not nodes:
        raise RuntimeError("No compatible OMK Node responded on USB (USB protocol v2 required)")
    if len(nodes) != 1:
        raise RuntimeError("Multiple OMK Nodes responded; specify --device or --node-id")
    return str(nodes[0]["device"]), str(nodes[0]["node_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)  # noqa: F405
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--status-timeout", type=float, default=60)
    parser.add_argument("--mqtt-broker", default=DEFAULT_MQTT_BROKER)  # noqa: F405
    parser.add_argument("--clear-wifi", action="store_true", help="development only: erase this Node's Gateway Wi-Fi credential")
    parser.add_argument("--node-id", help="select the physical OMK Node ID; with --device both must match")
    args = parser.parse_args()
    if args.timeout <= 0 or args.status_timeout <= 0:
        parser.error("--timeout and --status-timeout must be positive")
    if args.clear_wifi and not (args.device or args.node_id):
        parser.error("--clear-wifi requires --device or --node-id")
    if args.node_id and not valid_node_id(args.node_id):  # noqa: F405
        parser.error("--node-id must be 12 lowercase hexadecimal characters")
    if args.node_id and not args.device:
        device, node_id = find_usb_node_by_id(args.node_id, args.timeout)  # noqa: F405
    else:
        device, node_id = find_node(args.device, args.timeout)
        if args.node_id and args.node_id != node_id:
            raise RuntimeError("Selected USB port does not match --node-id")
    if args.clear_wifi:
        if clear_wifi(device, args.timeout, expected_node_id=node_id) != node_id:
            raise RuntimeError("Node identity changed while clearing Wi-Fi")
        wait_for_rebooted_identify(device, node_id, args.status_timeout)  # noqa: F405
        print(f"USB Wi-Fi credential cleared for node_id={node_id}.")
        return 0
    ssid, password = read_gateway_wifi(args.profile)  # noqa: F405
    if provision(device, ssid, password, args.timeout, expected_node_id=node_id) != node_id:  # noqa: F405
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
