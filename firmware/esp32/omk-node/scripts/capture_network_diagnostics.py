#!/usr/bin/env python3
"""Optional Linux native USB Serial/JTAG capture. Never resets or provisions.

No modem-line ioctl, esptool, MQTT dependency, or third-party Python package.
Pages are independent observations, not an atomic multi-page snapshot.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import termios
import time


def request(fd, node, view, page, timeout=1.0):
    os.write(fd, f"OMKDIAG {view} {page}\n".encode("ascii"))
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            raise OSError("USB disconnected")
        buffer.extend(chunk)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue  # Console logs or an interrupted line.
            if not isinstance(record, dict) or record.get("nd") != 1:
                continue
            if record.get("node") != node:
                raise ValueError("USB Node ID mismatch; capture stopped")
            if record.get("v") == view and record.get("p") == page:
                return record
        if len(buffer) > 8192:
            buffer.clear()
    return {"nd_timeout": True, "node": node, "v": view, "p": page}


def capture(port, node, output, interval, once):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = (attrs[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.HUPCL)) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        # HUPCL stays cleared on close. Do not touch DTR/RTS or auto-reconnect.
        out_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(out_fd, "a", encoding="utf-8", buffering=1) as stream:
            while True:
                started = time.monotonic()
                for view in range(3):
                    for page in range(27):
                        record = request(fd, node, view, page)
                        stream.write(json.dumps({"received_at": datetime.now(timezone.utc).isoformat(),
                                                 "diagnostic": record}) + "\n")
                        if record.get("nd_timeout"):
                            raise TimeoutError("No diagnostic response; check firmware/port, then retry manually")
                if once:
                    return
                time.sleep(max(0, interval - (time.monotonic() - started)))
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Native USB Serial/JTAG /dev/serial/by-id/... path")
    parser.add_argument("--node", required=True, help="Expected 12 lowercase hexadecimal digits")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if len(args.node) != 12 or any(c not in "0123456789abcdef" for c in args.node):
        parser.error("--node must be 12 lowercase hexadecimal digits")
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    try:
        capture(args.port, args.node, args.output, args.interval, args.once)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TimeoutError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
