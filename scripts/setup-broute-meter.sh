#!/usr/bin/env bash

# Install the unprivileged B-route service and its narrowly scoped USB helper.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
SERVICE='omk-broute-meter.service'
SUDO=()

if ((EUID != 0)); then SUDO=(sudo); fi
[[ -f "$OMK_ROOT/systemd/omk-broute-meter.service.in" ]] || { echo 'Unit template is missing.' >&2; exit 1; }
[[ -x "$OMK_ROOT/scripts/reset-rs-wsuha-p-usb.sh" ]] || { echo 'USB reset helper is missing or not executable.' >&2; exit 1; }

render_unit() {
  sed -e "s|@OMK_ROOT@|$OMK_ROOT|g" -e "s|@OMK_USER@|$TARGET_USER|g" -e "s|@OMK_GROUP@|$TARGET_GROUP|g" "$OMK_ROOT/systemd/omk-broute-meter.service.in"
}
if [[ ${1:-} == --print-unit ]]; then render_unit; exit 0; fi

ensure_runtime_directory() {
  local directory="$1" ownership

  if [[ -d "${directory}" ]]; then
    ownership="$(stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "${directory}")"
    printf 'Runtime directory already exists; preserving it: %s (%s)\n' "${directory}" "${ownership}"
    return
  fi
  if [[ -e "${directory}" ]]; then
    printf 'ERROR: Runtime directory path is occupied by a non-directory: %s\n' "${directory}" >&2
    return 1
  fi

  printf 'Creating runtime directory: %s\n' "${directory}"
  mkdir -p "${directory}"
  "${SUDO[@]}" chown "${TARGET_USER}:${TARGET_GROUP}" "${directory}"
}

# Keep B-route setup independently runnable. Existing runtime data and ownership
# are preserved; only missing directories are created before unit installation.
ensure_runtime_directory "${OMK_ROOT}/data/broute-meter"
ensure_runtime_directory "${OMK_ROOT}/logs/broute-meter"

"${SUDO[@]}" install -d -m 0755 /usr/local/lib/omk /etc/sudoers.d
"${SUDO[@]}" install -o root -g root -m 0755 "$OMK_ROOT/scripts/reset-rs-wsuha-p-usb.sh" /usr/local/lib/omk/reset-rs-wsuha-p-usb
printf '%s ALL=(root) NOPASSWD: /usr/local/lib/omk/reset-rs-wsuha-p-usb\n' "$TARGET_USER" | "${SUDO[@]}" tee /etc/sudoers.d/omk-rs-wsuha-p-reset >/dev/null
"${SUDO[@]}" chmod 0440 /etc/sudoers.d/omk-rs-wsuha-p-reset
"${SUDO[@]}" visudo -cf /etc/sudoers.d/omk-rs-wsuha-p-reset

temporary="$(mktemp)"; trap 'rm -f -- "$temporary"' EXIT
render_unit > "$temporary"
"${SUDO[@]}" install -m 0644 "$temporary" "/etc/systemd/system/$SERVICE"
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now "$SERVICE"
echo "Installed $SERVICE. Check: sudo systemctl status $SERVICE --no-pager"
