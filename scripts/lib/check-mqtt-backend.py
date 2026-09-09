#!/usr/bin/env python3
"""Require a real MQTT 3.1.1 CONNACK from the local production broker."""
import socket
import sys
try:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    if host not in ('127.0.0.1', '192.168.50.1'):
        raise ValueError('invalid local backend address')
    with socket.create_connection((host, 1883), timeout=3) as stream:
        stream.settimeout(3)
        stream.sendall(b'\x10\x19\x00\x04MQTT\x04\x02\x00\x0a\x00\x0domk-preflight')
        response = b''
        while len(response) < 4:
            part = stream.recv(4 - len(response))
            if not part:
                break
            response += part
        if response != b'\x20\x02\x00\x00':
            raise ValueError('broker did not accept MQTT CONNECT')
        stream.sendall(b'\xe0\x00')
except (OSError, ValueError) as error:
    print(f'FAIL: local MQTT backend: {error}', file=sys.stderr)
    sys.exit(1)
