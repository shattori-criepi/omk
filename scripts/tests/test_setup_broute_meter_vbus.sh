#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="$ROOT/scripts/setup-broute-meter.sh"

# Source only function definitions; setup's main guard prevents installation.
# shellcheck source=/dev/null
source "$SETUP"

sudoers="$(render_sudoers)"
expected="${TARGET_USER} ALL=(root) NOPASSWD: ${HELPER_DEST}, ${VBUS_HELPER_DEST}"
[[ "$sudoers" == "$expected" ]]
[[ "$sudoers" != *'/usr/sbin/uhubctl'* ]]

# Check the installation contract without writing host /usr/local or sudoers.
grep -Fq 'install -o root -g root -m 0755 "${VBUS_HELPER_SOURCE}" "${VBUS_HELPER_DEST}"' "$SETUP"
grep -Fq "stat -c '%u:%g:%a' \"\${VBUS_HELPER_DEST}\"" "$SETUP"
grep -Fq '"${SUDO[@]}" visudo -cf "${temporary}"' "$SETUP"
grep -Fq 'for package in python3 python3-venv python3-pip uhubctl' "$SETUP"

temporary_sudoers="$(mktemp)"
temporary_dir="$(mktemp -d)"
trap 'rm -f -- "$temporary_sudoers"; rm -rf -- "$temporary_dir"' EXIT
printf '%s\n' "$sudoers" > "$temporary_sudoers"
visudo -cf "$temporary_sudoers" >/dev/null

# Exercise helper installation/idempotency in a private temporary directory.
VBUS_HELPER_DEST="$temporary_dir/cycle-gateway-usb-vbus"
ensure_system_directory() { :; }
ensure_root_metadata() { [[ "$(stat -c '%a' "$1")" == "$2" ]]; }
install() {
  cp -- "${@: -2:1}" "${@: -1}"
  chmod 755 -- "${@: -1}"
}
install_vbus_helper
[[ "$(stat -c '%a' "$VBUS_HELPER_DEST")" == '755' ]]
second_install="$(install_vbus_helper)"
[[ "$second_install" == *'identical; preserving'* ]]
cmp -s "$VBUS_HELPER_SOURCE" "$VBUS_HELPER_DEST"

dry_run="$($SETUP --dry-run)"
[[ "$dry_run" == *'uhubctl'* ]]
[[ "$dry_run" == *'USB VBUS helper'* ]]
echo 'PASS: VBUS setup rendering, dry-run, ownership/mode, and sudoers contract.'
