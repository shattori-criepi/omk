#!/usr/bin/env bash
set -euo pipefail
usage() { echo "usage: $0 <atom-s3-lite> <port>" >&2; }
fail() { echo "$*" >&2; exit 1; }
env_name="${1:-}"; port="${2:-}"
[[ -n "$env_name" ]] || { usage; exit 2; }
[[ "$env_name" == "atom-s3-lite" ]] || {
  echo "unsupported Node board: $env_name. The only supported Node board is atom-s3-lite." >&2
  exit 2
}
[[ -n "$port" ]] || { echo 'Specify serial port as second argument.' >&2; usage; exit 2; }
[[ $# -eq 2 ]] || { usage; exit 2; }
root="$(cd "$(dirname "$0")/.." && pwd)"; project="$root/firmware/esp32/omk-node"; store="$root/data/provisioning/nodes"
pio=("${PLATFORMIO_CMD:-pio}")
pio_launcher="$(command -v "${pio[0]}")" || fail "Cannot locate PlatformIO CLI: ${pio[0]}"

# Prefer an explicit interpreter. Otherwise use the interpreter that launches
# the PlatformIO CLI, which has the dependencies required by its esptool package.
platformio_python="${PLATFORMIO_PYTHON:-}"
if [[ -z "$platformio_python" ]]; then
  pio_shebang="$(head -n 1 "$pio_launcher")"
  [[ "$pio_shebang" == '#!'* ]] || fail "Cannot determine PlatformIO Python; set PLATFORMIO_PYTHON"
  platformio_python="${pio_shebang#\#!}"
fi
[[ -x "$platformio_python" && ! -d "$platformio_python" ]] || fail "PlatformIO Python is not executable; set PLATFORMIO_PYTHON to its interpreter path."

# A first build installs the platform and its tools. Do not resolve esptool
# before this step: a new PlatformIO Core directory need not contain it yet.
"${pio[@]}" run -d "$project" -e "$env_name" || fail 'PlatformIO firmware build failed; flashing was not started.'
esptool_py="${ESPTOOL_PY:-}"
if [[ -z "$esptool_py" ]]; then
  # Use the same package resolver as espressif32's uploader, including project
  # package versions/paths and PLATFORMIO_CORE_DIR. Never install a system tool.
  esptool_py="$("$platformio_python" - "$project" "$env_name" <<'PY'
import os
from pathlib import Path
import sys
from platformio.platform.factory import PlatformFactory

os.chdir(sys.argv[1])
platform = PlatformFactory.from_env(sys.argv[2])
package = platform.get_package_dir("tool-esptoolpy")
if not package:
    sys.exit(1)
print(Path(package) / "esptool.py")
PY
)" || fail 'Cannot resolve PlatformIO esptool.py after build; check the PlatformIO environment or set ESPTOOL_PY.'
fi
[[ -f "$esptool_py" ]] || fail "Cannot locate PlatformIO esptool.py after build: $esptool_py"
esptool=("$platformio_python" "$esptool_py")

# esptool may report the same MAC before and after starting its stub. Keep
# unique values; multiple identities or any malformed line must fail below.
mac="$("${esptool[@]}" --port "$port" read_mac | awk '/MAC:/ { if ($1 != "MAC:" || NF != 2) print "invalid"; else print tolower($2) }' | LC_ALL=C sort -u)" || fail 'Cannot read ESP MAC.'
[[ "$mac" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]] || fail 'Cannot read a valid ESP MAC.'
node_id="$("$platformio_python" - "$mac" <<'PY'
import sys
mac=bytes.fromhex(sys.argv[1].replace(':',''))
h=14695981039346656037
for b in mac: h=((h^b)*1099511628211)&((1<<64)-1)
print(f'{h & ((1<<48)-1):012x}')
PY
)" || fail 'Cannot generate Node ID from ESP MAC.'
mkdir -p "$store" || fail 'Cannot create provisioning credential directory.'
chmod 700 "$root/data/provisioning" "$store" || fail 'Cannot secure provisioning credential directory.'
credential="$store/$node_id.json"
if [[ ! -f "$credential" ]]; then
  umask 077
  record_dir="$(mktemp -d "${TMPDIR:-/tmp}/omk-pop.XXXXXXXX")" || fail 'Cannot create factory record temporary directory.'
  credential_tmp=''
  cleanup() {
    rm -f -- "$record_dir/record.bin"
    [[ -z "$credential_tmp" ]] || rm -f -- "$credential_tmp"
    rmdir -- "$record_dir"
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  secret="$(openssl rand -hex 32)" || fail 'Cannot generate provisioning secret.'
  [[ "$secret" =~ ^[[:xdigit:]]{64}$ ]] || fail 'Cannot generate a valid provisioning secret.'
  credential_tmp="$(mktemp "$store/.credential.XXXXXXXX")" || fail 'Cannot create provisioning credential file.'
  printf '{\n  "node_id": "%s",\n  "provisioning_secret": "%s",\n  "board": "%s"\n}\n' "$node_id" "$secret" "$env_name" > "$credential_tmp" || fail 'Cannot write provisioning credential.'
  chmod 600 "$credential_tmp" || fail 'Cannot secure provisioning credential.'
  mv -- "$credential_tmp" "$credential" || fail 'Cannot save provisioning credential.'
  credential_tmp=''
  # First flash only: factory record is consumed and verified by firmware.
  printf 'OMKP' > "$record_dir/record.bin" || fail 'Cannot create factory provisioning record.'
  printf '%s' "$secret" | xxd -r -p >> "$record_dir/record.bin" || fail 'Cannot encode factory provisioning record.'
  unset secret
  "${esptool[@]}" --port "$port" write_flash 0xf000 "$record_dir/record.bin" || fail "Factory provisioning record write failed; credential retained at $credential. Verify factory provisioning before retrying."
fi
"${pio[@]}" run -d "$project" -e "$env_name" -t upload --upload-port "$port" || fail 'PlatformIO firmware upload failed; provisioning credential was retained.'
chmod 600 "$credential" || fail 'Cannot secure provisioning credential.'
printf 'Node %s flashed; credential saved at %s\n' "$node_id" "$credential"
