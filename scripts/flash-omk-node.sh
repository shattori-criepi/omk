#!/usr/bin/env bash
set -euo pipefail
usage() {
  echo "usage: $0 [--initial-setup | --reinitialize] <atom-s3-lite> <port>" >&2
  echo 'Default: update only with a saved Node credential.' >&2
  echo '--initial-setup: assert a known fresh, never-provisioned AtomS3 Lite; generate and write a NEW credential/PoP. Never use for recovery or an unknown Node.' >&2
  echo '--reinitialize: DESTRUCTIVE re-setup of an existing AtomS3 Lite. Replace its PoP, clear Logical ID and old Wi-Fi, and provision this Gateway AP over USB. Node and attached sensors must be registered again. No old credential backup required. Retry with this same option after failure.' >&2
}
fail() { echo "$*" >&2; exit 1; }
initial_setup=false
reinitialize=false
if [[ "${1:-}" == --initial-setup ]]; then
  initial_setup=true
  shift
fi
if [[ "${1:-}" == --reinitialize ]]; then
  reinitialize=true
  shift
fi
if "$initial_setup" && "$reinitialize"; then usage; exit 2; fi
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
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
if "$reinitialize"; then
  printf '%s\n' 'REINITIALIZE: replacing Node provisioning credential, clearing Logical ID and old Wi-Fi, and configuring this Gateway Wi-Fi. Register the Node/attached sensors again afterward.' >&2
  setup_python="${SYSTEM_MANAGER_PYTHON:-$root/services/system-manager/.venv/bin/python}"
  [[ -x "$setup_python" ]] || fail 'System-manager Python is required (esptool installed); set SYSTEM_MANAGER_PYTHON.'
  exec "$setup_python" "$root/scripts/reinitialize_omk_node.py" --reinitialize --device "$port" --build-dir "$project/.pio/build/$env_name"
fi
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

# Inspect the selected port afresh at each write boundary. Do not print raw
# esptool output: it contains the device MAC. Board identity remains the user's
# assertion; an ESP32-S3 chip alone does not identify an AtomS3 Lite.
inspect_device() {
  local output
  output="$("${esptool[@]}" --port "$port" read_mac 2>&1)" || {
    echo 'Cannot read ESP MAC/chip; check the selected port and retry.' >&2
    return 1
  }
  printf '%s\n' "$output" | "$platformio_python" -c '
import re
import sys

def fail(message):
    sys.exit(message)

lines = sys.stdin.read().splitlines()
chips = [line.strip() for line in lines if "Chip is" in line]
if not chips or any(not re.fullmatch(r"Chip is [A-Za-z0-9-]+(?: .+)?", line) for line in chips):
    fail("Cannot read valid ESP chip information.")
families = {line.split()[2] for line in chips}
if len(families) != 1:
    fail("Cannot read unambiguous ESP chip information.")
if families != {"ESP32-S3"}:
    fail("Unsupported chip; ESP32-S3 is required.")
reports = [line.strip() for line in lines if "MAC:" in line]
if not reports or any(not re.fullmatch(r"MAC: ([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", line) for line in reports):
    fail("Cannot read a valid ESP MAC.")
macs = {line.split()[1].lower() for line in reports}
if len(macs) != 1:
    fail("Ambiguous MAC output; refusing to write.")
print(macs.pop())
'
}
confirm_identity() {
  local current_mac
  current_mac="$(inspect_device)" || fail 'Device identity recheck failed; no further writes will be started.'
  [[ "$current_mac" == "$mac" ]] || fail 'Device identity changed; no further writes will be started.'
}
mac="$(inspect_device)" || exit 1
printf 'ESP32-S3 chip confirmed; AtomS3 Lite board is user-specified.\n'
node_id="$("$platformio_python" - "$mac" <<'PY'
import sys
mac=bytes.fromhex(sys.argv[1].replace(':',''))
h=14695981039346656037
for b in mac: h=((h^b)*1099511628211)&((1<<64)-1)
print(f'{h & ((1<<48)-1):012x}')
PY
)" || fail 'Cannot generate Node ID from ESP MAC.'
credential="$store/$node_id.json"
if [[ -e "$credential" || -L "$credential" ]]; then
  [[ -f "$credential" && ! -L "$credential" ]] || fail 'Saved credential is not a regular file. No firmware or credential/PoP write was started; restore the original credential.'
  "$initial_setup" && fail 'A saved credential already exists. No firmware or credential/PoP write was started. Use normal update without --initial-setup; do not delete the credential.'
  # A corrupt/wrong backup is not permission to create a replacement secret.
  "$platformio_python" - "$credential" "$node_id" <<'PY' || fail 'Saved credential is invalid, unreadable or re-setup is pending. No firmware or credential/PoP write was started; use --reinitialize to explicitly reset or retry this Node.'
import json
import re
import sys

try:
    with open(sys.argv[1]) as stream:
        record = json.load(stream)
    valid = (isinstance(record, dict)
             and record.get("node_id") == sys.argv[2]
             and record.get("board") == "atom-s3-lite"
             and record.get("setup_state") != "reinitialize_pending"
             and isinstance(record.get("provisioning_secret"), str)
             and re.fullmatch(r"[0-9a-fA-F]{64}", record["provisioning_secret"]))
except (OSError, ValueError):
    valid = False
sys.exit(0 if valid else 1)
PY
elif ! "$initial_setup"; then
  fail "No saved Gateway credential for Node ${node_id}. Cannot safely update: missing credential does not prove a fresh Node. No firmware was written; credential/PoP and NVS were not changed by this script. To intentionally replace this existing Node's credential and clear Wi-Fi/Logical ID, use --reinitialize. Only for a known fresh, never-provisioned AtomS3 Lite, use --initial-setup."
fi
mkdir -p "$store" || fail 'Cannot create provisioning credential directory.'
chmod 700 "$root/data/provisioning" "$store" || fail 'Cannot secure provisioning credential directory.'
if "$initial_setup"; then
  printf '%s\n' 'INITIAL SETUP: you asserted this is a fresh, never-provisioned AtomS3 Lite.' 'A NEW credential/PoP will be generated and written to factory flash; firmware will import it into NVS. This would replace an existing PoP if used on an existing Node. Freshness cannot be verified automatically.' >&2
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
  # First flash only: factory record is consumed and verified by firmware.
  printf 'OMKP' > "$record_dir/record.bin" || fail 'Cannot create factory provisioning record.'
  printf '%s' "$secret" | xxd -r -p >> "$record_dir/record.bin" || fail 'Cannot encode factory provisioning record.'
  unset secret
  confirm_identity
  # Persist only after identity confirmation, but before a write attempt: a
  # partially successful write must remain recoverable with the same secret.
  # Exclusive publication: do not replace a credential created meanwhile.
  ln -T -- "$credential_tmp" "$credential" || fail 'Cannot save provisioning credential without overwriting an existing path; no factory write was started.'
  rm -f -- "$credential_tmp"
  credential_tmp=''
  "${esptool[@]}" --port "$port" --chip esp32s3 write_flash 0xf000 "$record_dir/record.bin" || fail "Factory provisioning record write failed; credential retained at $credential. Verify factory provisioning before retrying."
fi
confirm_identity
"${pio[@]}" run -d "$project" -e "$env_name" -t upload --upload-port "$port" || fail 'PlatformIO firmware upload failed; provisioning credential was retained.'
chmod 600 "$credential" || fail 'Cannot secure provisioning credential.'
printf 'Node %s flashed; credential saved at %s\n' "$node_id" "$credential"
