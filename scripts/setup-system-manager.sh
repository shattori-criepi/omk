#!/usr/bin/env bash
# Install the authenticated, unprivileged host API for B-route credential rotation.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
SERVICE_NAME="omk-system-manager.service"
UNIT_TEMPLATE="${OMK_ROOT}/systemd/${SERVICE_NAME}.in"
UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}"
ENV_DIR="/etc/omk"
ENV_FILE="${ENV_DIR}/system-manager.env"
DASHBOARD_ENV_FILE="${ENV_DIR}/dashboard-system-manager.env"
SUDOERS_DEST="/etc/sudoers.d/omk-system-manager"
VENV_PATH="${OMK_ROOT}/services/system-manager/.venv"
SYSTEMCTL_PATH="$(command -v systemctl || true)"
SUDO=(sudo)

log() { printf '[omk-system-manager-setup] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

[[ "$(uname -s)" == Linux ]] || fail 'Linux is required.'
[[ -n "${SYSTEMCTL_PATH}" && "${SYSTEMCTL_PATH}" == /* ]] || fail 'An absolute systemctl path is required.'
[[ -f "${UNIT_TEMPLATE}" ]] || fail "Missing unit template: ${UNIT_TEMPLATE}"
[[ -f "${OMK_ROOT}/services/system-manager/requirements.txt" ]] || fail 'Missing system-manager requirements.'
[[ -d "${OMK_ROOT}/broute-meter/config" ]] || fail 'Missing broute-meter config directory.'
for command_name in python3 sudo install sed visudo mktemp openssl; do command -v "${command_name}" >/dev/null 2>&1 || fail "Required command is unavailable: ${command_name}"; done
id "${TARGET_USER}" >/dev/null 2>&1 || fail "Target user does not exist: ${TARGET_USER}"
render_unit() {
  sed -e "s|@OMK_ROOT@|${OMK_ROOT}|g" \
      -e "s|@OMK_USER@|${TARGET_USER}|g" \
      -e "s|@OMK_GROUP@|${TARGET_GROUP}|g" \
      -e "s|@ENV_FILE@|${ENV_FILE}|g" \
      -e "s|@SYSTEMCTL_PATH@|${SYSTEMCTL_PATH}|g" "${UNIT_TEMPLATE}"
}

render_sudoers() {
  printf '%s ALL=(root) NOPASSWD: %s restart omk-broute-meter.service, %s is-active omk-broute-meter.service\n' \
    "${TARGET_USER}" "${SYSTEMCTL_PATH}" "${SYSTEMCTL_PATH}"
}

ensure_token_file() {
  if [[ -e "${ENV_FILE}" ]]; then
    [[ -f "${ENV_FILE}" ]] || fail "Token path is not a regular file: ${ENV_FILE}"
    "${SUDO[@]}" chown root:root "${ENV_FILE}"
    "${SUDO[@]}" chmod 0600 "${ENV_FILE}"
    # This file is intentionally root-only. Validate only that a non-empty
    # token assignment exists; never read or print its value as the caller.
    "${SUDO[@]}" grep -q '^OMK_SYSTEM_MANAGER_TOKEN=.' "${ENV_FILE}" ||
      fail "Token file has no OMK_SYSTEM_MANAGER_TOKEN value: ${ENV_FILE}"
    log "Preserving existing root-only token file."
    return
  fi
  temporary="$(mktemp)"
  trap 'rm -f -- "${temporary}"' RETURN
  token="$(openssl rand -hex 32)"
  printf 'OMK_SYSTEM_MANAGER_TOKEN=%s\n' "${token}" > "${temporary}"
  "${SUDO[@]}" install -o root -g root -m 0600 "${temporary}" "${ENV_FILE}"
  rm -f -- "${temporary}"
  trap - RETURN
  log "Created root-only system-manager token file."
}

sync_dashboard_token_file() {
  # Compose reads env_file as TARGET_USER. Copy the one-line token without
  # printing it; this is an environment injection file, not a container mount.
  "${SUDO[@]}" install -o root -g "${TARGET_GROUP}" -m 0640 "${ENV_FILE}" "${DASHBOARD_ENV_FILE}"
  log "Updated Dashboard system-manager token environment file."
}

"${SUDO[@]}" install -d -o root -g root -m 0755 "${ENV_DIR}"
log 'Creating or reusing Python virtual environment.'
"${SUDO[@]}" -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"
log 'Installing system-manager requirements.'
"${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
"${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/system-manager/requirements.txt"
ensure_token_file
sync_dashboard_token_file

temporary_unit="$(mktemp)"
temporary_sudoers="$(mktemp)"
trap 'rm -f -- "${temporary_unit}" "${temporary_sudoers}"' EXIT
render_unit > "${temporary_unit}"
render_sudoers > "${temporary_sudoers}"
"${SUDO[@]}" visudo -cf "${temporary_sudoers}"
"${SUDO[@]}" install -o root -g root -m 0644 "${temporary_unit}" "${UNIT_DEST}"
"${SUDO[@]}" install -o root -g root -m 0440 "${temporary_sudoers}" "${SUDOERS_DEST}"
"${SUDO[@]}" visudo -cf "${SUDOERS_DEST}"
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable "${SERVICE_NAME}"
if "${SUDO[@]}" systemctl is-active --quiet "${SERVICE_NAME}"; then
  log "Restarting active ${SERVICE_NAME} to apply the installed unit."
  "${SUDO[@]}" systemctl restart "${SERVICE_NAME}"
else
  log "Starting inactive ${SERVICE_NAME}."
  "${SUDO[@]}" systemctl start "${SERVICE_NAME}"
fi
"${SUDO[@]}" systemctl --no-pager --full status "${SERVICE_NAME}"
log "Installed ${SERVICE_NAME}. Token file: ${ENV_FILE} (root-only; do not print or commit it)."
