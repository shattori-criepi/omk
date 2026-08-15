#!/usr/bin/env bash
set -euo pipefail
env_name="${1:?usage: flash-omk-node.sh <atom-s3-lite|m5stick-c> [port]}"; port="${2:-}"
root="$(cd "$(dirname "$0")/.." && pwd)"; project="$root/firmware/esp32/omk-node"; store="$root/data/provisioning/nodes"
mkdir -p "$store"; chmod 700 "$root/data/provisioning" "$store"
pio=("${PLATFORMIO_CMD:-pio}")
fail() { echo "$*" >&2; exit 1; }

# Prefer an explicit interpreter. Otherwise use the interpreter that launches
# the PlatformIO CLI, which has the dependencies required by its esptool package.
platformio_python="${PLATFORMIO_PYTHON:-}"
if [[ -z "$platformio_python" ]]; then
  pio_launcher="$(command -v "${pio[0]}")" || fail "Cannot locate PlatformIO CLI: ${pio[0]}"
  pio_shebang="$(head -n 1 "$pio_launcher")"
  [[ "$pio_shebang" == '#!'* ]] || fail "Cannot determine PlatformIO Python; set PLATFORMIO_PYTHON"
  platformio_python="${pio_shebang#\#!}"
fi
[[ -x "$platformio_python" ]] || fail "PlatformIO Python is not executable: $platformio_python"

platformio_core_dir="${PLATFORMIO_CORE_DIR:-$HOME/.platformio}"
esptool_py="${ESPTOOL_PY:-$platformio_core_dir/packages/tool-esptoolpy/esptool.py}"
[[ -f "$esptool_py" ]] || fail "Cannot locate PlatformIO esptool.py: $esptool_py"
esptool=("$platformio_python" "$esptool_py")

"${pio[@]}" run -d "$project" -e "$env_name"
# The caller must provide a serial port until board-specific auto-detection is added.
[[ -n "$port" ]] || { echo 'Specify serial port as second argument.' >&2; exit 2; }
mac="$("${esptool[@]}" --port "$port" read_mac | awk '/MAC:/{print $NF}')"
[[ -n "$mac" ]] || { echo 'Cannot read ESP MAC.' >&2; exit 1; }
node_id="$("$platformio_python" - "$mac" <<'PY'
import sys
mac=bytes.fromhex(sys.argv[1].replace(':',''))
h=14695981039346656037
for b in mac: h=((h^b)*1099511628211)&((1<<64)-1)
print(f'{h & ((1<<48)-1):012x}')
PY
)"
credential="$store/$node_id.json"
if [[ ! -f "$credential" ]]; then
  secret="$(openssl rand -hex 32)"
  umask 077; printf '{\n  "node_id": "%s",\n  "provisioning_secret": "%s",\n  "board": "%s"\n}\n' "$node_id" "$secret" "$env_name" > "$credential"
  # First flash only: factory record is consumed and verified by firmware.
  printf 'OMKP' > /tmp/omk-pop.bin; printf '%s' "$secret" | xxd -r -p >> /tmp/omk-pop.bin
  "${esptool[@]}" --port "$port" write_flash 0xf000 /tmp/omk-pop.bin
  rm -f /tmp/omk-pop.bin
fi
"${pio[@]}" run -d "$project" -e "$env_name" -t upload --upload-port "$port"
chmod 600 "$credential"; printf 'Node %s flashed; credential saved at %s\n' "$node_id" "$credential"
