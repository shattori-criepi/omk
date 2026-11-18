#!/usr/bin/env python3
"""Decode an OMK DIAG1 raw partition dump without a device or ESP-IDF install.

The storage envelope is independent of the diagnostic payload schema. A valid
footer alone never makes a record valid: header, bounds and both CRCs are checked.
Occupied invalid records are reported explicitly and must not be reused.
"""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import struct
import sys
import zlib

SLOT_SIZE = 4096
FLASH_SIZE = SLOT_SIZE * 2
HEADER_SIZE = 32
PAYLOAD_MAX = 4056
MAGIC = b"OMKDIAG1"
COMMIT_MAGIC = 0x31474C44


def decoded_payload(payload):
    """Load the colocated firmware-schema decoder without changing sys.path."""
    location = Path(__file__).with_name("diag_payload.py")
    if not location.is_file():
        raise ValueError("payload decoder is not installed beside diag_flash.py")
    name = "omk_diag_payload_decoder"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, location)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.decode_payload(payload)


def decode_slot(raw: bytes, slot: int) -> dict:
    result = {"slot": slot, "offset": slot * SLOT_SIZE, "state": "invalid", "errors": []}
    if len(raw) != SLOT_SIZE:
        result["errors"].append("truncated_sector")
        return result
    if raw == b"\xff" * SLOT_SIZE:
        result["state"] = "blank"
        return result
    magic, version, stored_slot, length, payload_crc, reserved, header_crc = struct.unpack_from("<8s6I", raw)
    commit, inverse = struct.unpack_from("<2I", raw, SLOT_SIZE - 8)
    computed_header_crc = zlib.crc32(raw[:28]) & 0xFFFFFFFF
    result["header"] = {
        "magic_hex": magic.hex(), "version": version, "slot": stored_slot,
        "payload_length": length, "payload_crc32": f"{payload_crc:08x}",
        "reserved": reserved, "header_crc32": f"{header_crc:08x}",
        "computed_header_crc32": f"{computed_header_crc:08x}",
    }
    result["commit"] = {"magic": f"{commit:08x}", "inverse": f"{inverse:08x}"}
    errors = result["errors"]
    if magic != MAGIC:
        errors.append("bad_magic_or_partial_header")
    if version != 1:
        errors.append("unsupported_version")
    if stored_slot != slot:
        errors.append("wrong_slot")
    if length > PAYLOAD_MAX:
        errors.append("payload_length_out_of_bounds")
    if reserved != 0:
        errors.append("reserved_not_zero")
    if computed_header_crc != header_crc:
        errors.append("header_crc_mismatch")
    if commit != COMMIT_MAGIC or inverse != (COMMIT_MAGIC ^ 0xFFFFFFFF):
        errors.append("uncommitted_or_partial_footer")
    if length <= PAYLOAD_MAX:
        payload = raw[HEADER_SIZE:HEADER_SIZE + length]
        computed_payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
        result["computed_payload_crc32"] = f"{computed_payload_crc:08x}"
        if computed_payload_crc != payload_crc:
            errors.append("payload_crc_mismatch")
        if not errors:
            result["state"] = "valid"
            result["payload_base64"] = base64.b64encode(payload).decode("ascii")
            try:
                result["diagnostic"] = decoded_payload(payload)
            except ValueError as error:
                result["payload_decode_error"] = str(error)
    return result


def decode_flash(raw: bytes) -> dict:
    slots = [decode_slot(raw[index * SLOT_SIZE:(index + 1) * SLOT_SIZE], index) for index in range(2)]
    return {
        "format": "omk_diag_raw_v1", "partition_offset": "0x210000",
        "expected_size": FLASH_SIZE, "actual_size": len(raw),
        "size_valid": len(raw) == FLASH_SIZE,
        "slots": slots,
        "boot_auto_arm_allowed": len(raw) == FLASH_SIZE and all(s["state"] == "blank" for s in slots),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="8192-byte raw partition from USB or esptool read_flash")
    parser.add_argument("--extract-prefix", type=Path,
                        help="write each valid payload to PREFIX-slot0.bin / PREFIX-slot1.bin")
    args = parser.parse_args()
    raw = args.dump.read_bytes()
    manifest = decode_flash(raw)
    if args.extract_prefix is not None and manifest["size_valid"]:
        for slot in manifest["slots"]:
            if slot["state"] == "valid":
                target = Path(f'{args.extract_prefix}-slot{slot["slot"]}.bin')
                # Refuse to silently overwrite previously recovered evidence.
                with target.open("xb") as output:
                    output.write(base64.b64decode(slot["payload_base64"]))
                slot["extracted_to"] = str(target)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["size_valid"] and all(s["state"] != "invalid" for s in manifest["slots"]) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"diag_flash: {error}", file=sys.stderr)
        sys.exit(2)
