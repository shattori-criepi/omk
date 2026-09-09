#!/usr/bin/env python3
"""Reject production Dashboard/MQTT host publishes outside loopback."""

from __future__ import annotations

import json
import sys


EXPECTED = {
    "mosquitto": {(1883, 1883, "127.0.0.1", "tcp")},
    "dashboard": {(8000, 8000, "127.0.0.1", "tcp")},
}


def normalized_port(port: object) -> tuple[int, int, str, str]:
    if not isinstance(port, dict):
        raise ValueError(f"unexpected non-object port entry: {port!r}")
    try:
        target = int(port["target"])
        published = int(port["published"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"incomplete published port entry: {port!r}") from error
    host_ip = str(port.get("host_ip") or "0.0.0.0")
    protocol = str(port.get("protocol") or "tcp")
    return target, published, host_ip, protocol


def main() -> int:
    try:
        config = json.load(sys.stdin)
        services = config["services"]
        for service, expected in EXPECTED.items():
            actual = {normalized_port(port) for port in services[service].get("ports", [])}
            if actual != expected:
                raise ValueError(
                    f"{service} publishes {sorted(actual)!r}; required exact loopback publish is {sorted(expected)!r}"
                )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: unsafe or invalid production port publishing: {error}", file=sys.stderr)
        return 1
    print("PASS: Dashboard and MQTT are published by Docker only on IPv4 loopback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
