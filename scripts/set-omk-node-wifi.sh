#!/usr/bin/env bash
# One-shot development tool for persisted STA credentials. It never erases an
# NVS partition and never writes the factory_secret partition.
set -euo pipefail
set +x
umask 077

board="${1:-}"
port="${2:-}"

case "$board" in
  atom-s3-lite) set_env="atom-s3-lite-wifi-set" ;;
  m5stick-c) set_env="m5stick-c-wifi-set" ;;
  *) echo "usage: $0 <atom-s3-lite|m5stick-c> <serial-port>" >&2; exit 2 ;;
esac
[[ -n "$port" ]] || { echo "serial port is required" >&2; exit 2; }

root="$(cd "$(dirname "$0")/.." && pwd)"
project="$root/firmware/esp32/omk-node"
header="$project/src/development_wifi_config.h"
build_dir="$project/.pio/build/$set_env"
pio=("${PLATFORMIO_CMD:-pio}")

[[ ! -e "$header" ]] || {
  echo "Refusing to overwrite development Wi-Fi header: $header" >&2
  exit 1
}

cleanup() {
  rm -f -- "$header"
  # This exact, generated environment is the only build output containing the
  # temporary credential image. It is always removed after success or failure.
  rm -rf -- "$build_dir"
  unset ssid psk
}
trap cleanup EXIT

read -r -p "OMK Wi-Fi SSID: " ssid
read -r -s -p "OMK Wi-Fi PSK: " psk
printf '\n'

if [[ -z "$ssid" || -z "$psk" ]]; then
  echo "SSID and PSK must not be empty" >&2
  exit 2
fi

# Generate byte arrays instead of placing either value in a command argument,
# build flag, log, or tracked file. Input is NUL-delimited so no shell quoting
# of the credential is required.
printf '%s\0%s\0' "$ssid" "$psk" | python3 -c '
import os
import sys

path = sys.argv[1]
ssid, separator, remainder = sys.stdin.buffer.read().partition(b"\0")
psk, separator2, trailing = remainder.partition(b"\0")
if not separator or not separator2 or trailing:
    raise SystemExit("invalid credential input")
if not 1 <= len(ssid) <= 32:
    raise SystemExit("SSID must be 1 through 32 UTF-8 bytes")
if not 8 <= len(psk) <= 64:
    raise SystemExit("PSK must be 8 through 64 UTF-8 bytes")
if b"\0" in ssid or b"\0" in psk:
    raise SystemExit("NUL is not allowed in credentials")

def byte_array(name, value):
    values = ", ".join(f"0x{byte:02x}" for byte in value)
    return f"static const uint8_t {name}[] = {{{values}}};\n"

with open(path, "x", encoding="ascii") as output:
    os.chmod(path, 0o600)
    output.write("#pragma once\n#include <stddef.h>\n#include <stdint.h>\n")
    output.write(byte_array("OMK_DEVELOPMENT_WIFI_SSID", ssid))
    output.write(f"static const size_t OMK_DEVELOPMENT_WIFI_SSID_LENGTH = {len(ssid)};\n")
    output.write(byte_array("OMK_DEVELOPMENT_WIFI_PSK", psk))
    output.write(f"static const size_t OMK_DEVELOPMENT_WIFI_PSK_LENGTH = {len(psk)};\n")
' "$header"

echo "Flashing one-shot Wi-Fi credential image for $board..."
"${pio[@]}" run -d "$project" -e "$set_env" -t upload --upload-port "$port"

# The image validates its write before returning from app_main. Give the MCU a
# short interval to run it before restoring the normal production image.
sleep 2

echo "Restoring normal $board firmware..."
"${pio[@]}" run -d "$project" -e "$board" -t upload --upload-port "$port"
echo "Production firmware restored. Verify the normal boot log for Wi-Fi and MQTT connection."
