#!/usr/bin/env bash
# Reset only the development-test registration state, then restore production
# firmware.  This script never erases the NVS partition or writes factory data.
set -euo pipefail

board="${1:-}"
port="${2:-}"
confirmation="${3:-}"

if [[ "$confirmation" != "--confirm" ]]; then
  echo "usage: $0 <atom-s3-lite|m5stick-c> <serial-port> --confirm" >&2
  exit 2
fi

case "$board" in
  atom-s3-lite) reset_env="atom-s3-lite-registration-reset" ;;
  m5stick-c) reset_env="m5stick-c-registration-reset" ;;
  *) echo "unsupported board: $board" >&2; exit 2 ;;
esac

[[ -n "$port" ]] || { echo "serial port is required" >&2; exit 2; }

root="$(cd "$(dirname "$0")/.." && pwd)"
project="$root/firmware/esp32/omk-node"
pio=("${PLATFORMIO_CMD:-pio}")

echo "Flashing one-shot registration-reset image for $board..."
"${pio[@]}" run -d "$project" -e "$reset_env" -t upload --upload-port "$port"

# PlatformIO resets after upload. The reset image only opens namespace omk,
# deletes registered/logical_id, commits, and reads both keys back. Give it
# time to finish before replacing it with the normal image.
sleep 2

echo "Restoring normal $board firmware..."
"${pio[@]}" run -d "$project" -e "$board" -t upload --upload-port "$port"
echo "Registration state reset image completed; production firmware restored."
