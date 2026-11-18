#!/usr/bin/env python3
"""DIAG1 Linux native USB-Serial/JTAG collection candidate; hardware acceptance required.

Default operation only reads. --arm first saves flash+live evidence, then requires
the literal --confirm ERASE_DIAGNOSTIC_EVIDENCE to erase the diagnostic partition.
Run with the existing PlatformIO Python environment which includes pyserial.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import zlib

CONFIRM = "ERASE_DIAGNOSTIC_EVIDENCE"


class CollectionError(RuntimeError):
    pass


class StaleToken(CollectionError):
    pass


def native_usb_port(path):
    """Inspect metadata only: never probe a TTY by opening it for identification."""
    import serial
    from serial.tools import list_ports
    if sys.platform != "linux" or serial.VERSION != "3.5":
        raise CollectionError("requires Linux cdc_acm and audited pySerial 3.5")
    resolved = os.path.realpath(path)
    matches = [p for p in list_ports.comports() if os.path.realpath(p.device) == resolved]
    if len(matches) != 1 or (matches[0].vid, matches[0].pid) != (0x303A, 0x1001):
        raise CollectionError("requires verified native Espressif USB-Serial/JTAG 303a:1001")
    driver = Path("/sys/class/tty") / Path(resolved).name / "device/driver"
    if driver.resolve().name != "cdc_acm":
        raise CollectionError("requires cdc_acm; do not use this policy for UART bridges")
    return resolved


def open_native_port(path, timeout):
    import serial
    import fcntl
    import struct
    import termios
    port = serial.Serial(port=None, baudrate=115200, timeout=min(timeout, 0.25),
                         write_timeout=timeout, exclusive=True, rtscts=False, dsrdtr=False)
    # Linux cdc_acm raises BOTH on open. Preserve that state: clearing DTR first
    # traverses (DTR=0, RTS=1), an S3 USJ reset state. These are CDC bits, not GPIO voltages.
    port.dtr = True
    port.rts = True
    port.port = path
    try:
        port.open()
        # Use this already-open FD; a separate stty would itself open/close the port.
        attrs = termios.tcgetattr(port.fileno())
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(port.fileno(), termios.TCSANOW, attrs)
        if termios.tcgetattr(port.fileno())[2] & termios.HUPCL:
            raise CollectionError("HUPCL could not be cleared")
        fcntl.ioctl(port.fileno(), termios.TIOCEXCL)
        bits = struct.unpack('I', fcntl.ioctl(port.fileno(), termios.TIOCMGET, struct.pack('I', 0)))[0]
        mask = termios.TIOCM_DTR | termios.TIOCM_RTS
        if bits & mask != mask:
            raise CollectionError("unexpected DTR/RTS state; refusing data commands")
        return port
    except BaseException:
        # No resets, reopen loop, or control-line cleanup. HUPCL remains cleared
        # once applied; restoring it before close would reintroduce the transition.
        port.close()
        raise


def check_live_boot(payload, expected_boot, expected_node):
    from diag_payload import decode_payload
    try:
        decoded = decode_payload(payload)
    except ValueError as error:
        raise CollectionError(str(error)) from error
    if decoded["boot_id"] != expected_boot or decoded["node_id"] != expected_node:
        raise CollectionError("live boot_id/node_id changed; raw evidence retained; acceptance FAILED")
    return {key: decoded[key] for key in ("boot_id", "node_id", "capture_ms", "reset_reason")}


class Session:
    def __init__(self, port, node, timeout=1.5, retries=3, transcript=None):
        self.port, self.node = port, node
        self.timeout, self.retries = timeout, retries
        self.transcript = transcript

    def request(self, command, *, validate=None, attempts=None, **fields):
        request = dict(protocol_version=2, command=command)
        if command != "identify":
            request["expected_node_id"] = self.node
        request.update(fields)
        wire = (json.dumps(request, separators=(",", ":")) + "\n").encode("ascii")
        if len(wire) > 256:
            raise CollectionError("request exceeds firmware line capacity")
        for _ in range(self.retries if attempts is None else attempts):
            if self.port.write(wire) != len(wire):
                raise CollectionError("incomplete USB request write")
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                line = self.port.readline(512)
                if self.transcript:
                    self.transcript(line)
                if not line.endswith(b"\n"):
                    continue
                try:
                    result = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue  # SDK log, mixed console bytes, or truncated reply.
                if not isinstance(result, dict) or result.get("protocol_version") != 2:
                    continue
                if result.get("node_id") != self.node:
                    raise CollectionError("USB Node identity differs from --node; no further commands sent")
                if result.get("status") == "node_identity_changed":
                    raise CollectionError("firmware rejected target Node identity")
                if command == "identify":
                    if "wifi_configured" not in result or result.get("status") != "ok":
                        continue
                elif result.get("command") != command:
                    continue
                state = result.get("status")
                if state == "stale_token":
                    raise StaleToken("live snapshot changed during download")
                if state not in ("ok", "accepted"):
                    raise CollectionError(f"{command}: firmware returned {state!r}")
                if validate is not None and not validate(result):
                    continue
                return result
        suffix = "; arm outcome unknown, collect again before considering another arm" if command == "diag_arm" else ""
        raise CollectionError(f"{command}: no valid response after bounded retries{suffix}")

    def read(self, space, length, token=0):
        if not isinstance(length, int) or not 0 < length <= (8192 if space == "flash" else 4056):
            raise CollectionError("invalid diagnostic length")
        output = bytearray()
        for offset in range(0, length, 48):
            count = min(48, length - offset)

            def valid(result):
                if result.get("offset") != offset or result.get("token") != token:
                    return False
                encoded, crc = result.get("data"), result.get("crc32")
                if not isinstance(encoded, str) or len(encoded) != count * 2:
                    return False
                if not isinstance(crc, str) or not re.fullmatch(r"[0-9a-fA-F]{8}", crc):
                    return False
                try:
                    data = bytes.fromhex(encoded)
                except ValueError:
                    return False
                return len(data) == count and zlib.crc32(data) == int(crc, 16)

            response = self.request("diag_read", validate=valid, space=space,
                                    offset=offset, count=count, **({"token": token} if space == "live" else {}))
            output.extend(bytes.fromhex(response["data"]))
        return bytes(output)


def collect(session, on_flash=None):
    identity = session.request("identify")
    # A scheduled persistence operation can occur while USB chunks are read.
    # Compare slot masks and mutation revision before/after and retry the complete dump if it changed.
    def signature(info):
        return tuple(info.get(k) for k in ("ready", "occupied", "valid", "attempt", "revision"))

    for _ in range(session.retries):
        before = session.request("diag_info")
        if before.get("flash") != 8192:
            raise CollectionError("unexpected diagnostic partition size")
        flash = session.read("flash", 8192)
        after = session.request("diag_info")
        if signature(before) == signature(after):
            break
    else:
        raise CollectionError("persistent slots kept changing during read; rerun collection")
    # Save persistent evidence immediately; an unavailable live endpoint must
    # not throw away a successfully recovered flash dump.
    if on_flash is not None:
        on_flash(flash)
    for _ in range(session.retries):
        live = session.request("diag_live")
        token = live.get("token")
        if not isinstance(token, int) or not 0 < token <= 0xFFFFFFFF:
            raise CollectionError("invalid live snapshot token")
        crc = live.get("crc32")
        if not isinstance(crc, str) or not re.fullmatch(r"[0-9a-fA-F]{8}", crc):
            raise CollectionError("missing live snapshot CRC")
        try:
            payload = session.read("live", live.get("length"), token)
            if zlib.crc32(payload) != int(crc, 16):
                continue  # Token reuse across reboot or a mixed snapshot.
            break
        except StaleToken:
            continue
    else:
        raise CollectionError("live snapshot repeatedly changed; persistent dump remains readable")
    return flash, payload, {"identity": identity, "info_before": before, "info_after": after, "live": live}


def write_new(path, data):
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def arm_backed_up(session, destination, flash, live, expected_boot):
    from diag_payload import decode_payload
    decoded = decode_payload(live)
    if decoded['schema'] not in (2, 3):
        raise CollectionError("LR2 guarded arm requires schema 2/3; old firmware remains read-only")
    if decoded['boot_id'] != expected_boot or decoded['node_id'] != session.node:
        raise CollectionError("backup identity mismatch")
    # Check durable files, not merely an in-memory buffer or a user-supplied CRC.
    for name, data in [('flash.bin', flash), ('live.bin', live)]:
        if (destination / name).read_bytes() != data:
            raise CollectionError("backup file mismatch")
    if not (destination / 'manifest.json').is_file():
        raise CollectionError("backup manifest missing")
    fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    before = session.request('diag_state')
    if before.get('boot_id') != expected_boot:
        raise CollectionError("boot changed before arm")
    result = session.request("diag_arm", attempts=1, confirm=CONFIRM,
                             expected_boot_id=expected_boot, backup_crc32=zlib.crc32(flash))
    write_new(destination / "arm-result.json", (json.dumps(result, indent=2) + "\n").encode())
    # Never retry arm. Read back BOTH sectors and the live observation identity.
    info = session.request('diag_info')
    blank = session.read('flash', 8192)
    after = session.request('diag_info')
    state = session.request('diag_state')
    report = dict(info_before=info, info_after=after, state=state,
                  all_ff=blank == b'\xff' * 8192, prior_state=before)
    write_new(destination / 'arm-verify.json', (json.dumps(report, indent=2) + '\n').encode())
    expected_arm = (before['arm'] + 1) & 0xffffffff or 1
    if (not report['all_ff'] or state.get('boot_id') != expected_boot or state.get('arm') != expected_arm or
            any(v.get('ready') != 1 or any(v.get(k) != 0 for k in ('occupied', 'valid', 'attempt', 'error'))
                for v in (info, after))):
        raise CollectionError("arm verification failed; evidence retained, do not repeat arm")
    print("LR2 armed: both 4096-byte slots verified empty; "
          f"boot={expected_boot}, arm={state['arm']}, start_ms={state['start_ms']}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="explicit /dev/serial/by-id/... or /dev/ttyACM...")
    parser.add_argument("--node", required=True, help="expected 12-hex physical Node ID")
    parser.add_argument("--expected-boot-id", required=True,
                        help="16-hex boot_id observed via existing MQTT/status BEFORE opening USB")
    parser.add_argument("--out-dir", type=Path, help="new output directory; an existing path is refused")
    parser.add_argument("--timeout", type=float, default=1.5, help="per-request deadline seconds (0.1..10)")
    parser.add_argument("--arm", action="store_true", help="after successful backup, erase ONLY diagnostic evidence")
    parser.add_argument("--confirm", help=f"required with --arm: {CONFIRM}")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-fA-F]{12}", args.node):
        parser.error("--node must contain exactly 12 hex digits")
    args.node = args.node.lower()
    if not re.fullmatch(r"[0-9a-fA-F]{16}", args.expected_boot_id):
        parser.error("--expected-boot-id must contain exactly 16 hex digits")
    args.expected_boot_id = args.expected_boot_id.lower()
    if not 0.1 <= args.timeout <= 10:
        parser.error("--timeout must be between 0.1 and 10 seconds")
    if args.arm and args.confirm != CONFIRM:
        parser.error(f"--arm requires --confirm {CONFIRM}; this deletes diagnostic evidence")
    if args.confirm and not args.arm:
        parser.error("--confirm is only meaningful with --arm")
    resolved_port = native_usb_port(args.port)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = args.out_dir or Path(f"diag-{args.node}-{timestamp}")
    destination.mkdir(parents=True, exist_ok=False)
    log_path = destination / "usb.log"
    with log_path.open("xb") as transcript, (destination / "lifecycle.jsonl").open("x") as lifecycle:
        logged = 0

        def phase(name, **fields):
            lifecycle.write(json.dumps(dict(event=name, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                            monotonic_ns=time.monotonic_ns(), **fields)) + "\n")
            lifecycle.flush()
            os.fsync(lifecycle.fileno())

        def log(line):
            nonlocal logged
            bounded = line[:max(0, 1048576 - logged)]
            transcript.write(bounded)
            logged += len(bounded)

        phase("before_open", port=resolved_port, expected_boot_id=args.expected_boot_id)
        port = None
        try:
            port = open_native_port(resolved_port, args.timeout)
            phase("after_open", dtr=True, rts=True, hupcl=False)
            session = Session(port, args.node, args.timeout, transcript=log)
            phase("before_collect")
            flash, live, manifest = collect(session, lambda data: write_new(destination / "flash.bin", data))
            write_new(destination / "live.bin", live)
            # Persist even a changed boot's payload before failing; never silently
            # accept a healthy new boot as the pre-open fault state.
            observed = check_live_boot(live, args.expected_boot_id, args.node)
            phase("after_collect", **observed)
            if int.from_bytes(live[:4], 'little') in (2, 3):
                manifest['state'] = session.request('diag_state')
            manifest.update(collected_at_utc=timestamp, port=args.port, node=args.node,
                            flash_crc32=f"{zlib.crc32(flash):08x}", live_crc32=f"{zlib.crc32(live):08x}",
                            expected_boot_id=args.expected_boot_id, observed_live=observed,
                            usb_policy="linux-cdc_acm-dtr1-rts1-nohupcl",
                            post_close_boot_verified=False)
            write_new(destination / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
            if args.arm:
                print(f"Diagnostic evidence backed up to {destination}. Erasing diagnostic slots only.", file=sys.stderr)
                arm_backed_up(session, destination, flash, live, args.expected_boot_id)
        except BaseException as error:
            phase("error", error=str(error))
            raise
        finally:
            try:
                if port is not None:
                    try:
                        phase("before_close")
                    finally:
                        port.close()
                    phase("after_close")
            finally:
                transcript.flush()
                os.fsync(transcript.fileno())
    print(destination)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ImportError, CollectionError) as error:
        print(f"diag_collect: {error}", file=sys.stderr)
        raise SystemExit(2)
