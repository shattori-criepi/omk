#!/usr/bin/env python3
"""Apply the exact reviewed patch to a build-local ESP-MQTT 1.1.0 copy."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def apply_patch(original: str, patch: str) -> str:
    lines = original.splitlines(keepends=True)
    output = []
    cursor = 0
    for line in patch.splitlines(keepends=True):
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@ "):
            match = re.fullmatch(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@\n", line)
            if not match:
                raise ValueError("Unsupported patch hunk")
            start = int(match[1]) - 1
            if start < cursor:
                raise ValueError("Overlapping patch hunks")
            output.extend(lines[cursor:start])
            cursor = start
        elif line.startswith((" ", "-")):
            if cursor >= len(lines) or lines[cursor] != line[1:]:
                raise ValueError("Patch context mismatch")
            if line[0] == " ":
                output.append(line[1:])
            cursor += 1
        elif line.startswith("+"):
            output.append(line[1:])
        else:
            raise ValueError("Unsupported patch line")
    output.extend(lines[cursor:])
    return "".join(output)


def prepare(source: Path, destination: Path) -> None:
    metadata = json.loads((ROOT / "patches/mqtt-1.1.0-diagnostics.json").read_text())
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != metadata["original_sha256"]:
        raise ValueError("ESP-MQTT source differs from reviewed 1.1.0; refusing build")
    patch = (ROOT / "patches/mqtt-1.1.0-diagnostics.patch").read_text()
    patched = apply_patch(data.decode(), patch).encode()
    if hashlib.sha256(patched).hexdigest() != metadata["patched_sha256"]:
        raise ValueError("Patched MQTT source hash mismatch; refusing build")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != patched:
        destination.write_bytes(patched)


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
