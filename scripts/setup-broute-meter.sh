#!/usr/bin/env bash

# Install the unprivileged B-route service and its narrowly scoped USB helper.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP=""
SERVICE='omk-broute-meter.service'
UNIT_TEMPLATE="${OMK_ROOT}/systemd/omk-broute-meter.service.in"
HELPER_SOURCE="${OMK_ROOT}/scripts/reset-rs-wsuha-p-usb.sh"
VENV_PATH="${OMK_ROOT}/broute-meter/.venv"
VENV_PYTHON="${VENV_PATH}/bin/python"
HELPER_DEST='/usr/local/lib/omk/reset-rs-wsuha-p-usb'
SUDOERS_DEST='/etc/sudoers.d/omk-rs-wsuha-p-reset'
UNIT_DEST="/etc/systemd/system/${SERVICE}"
LOG_DIR="${OMK_ROOT}/logs/setup"
DRY_RUN=false
PRINT_UNIT=false
LOG_FILE=""
SUDO=()
PREFLIGHT_FAILURES=()
PREFLIGHT_OK=true

usage() {
  cat <<'EOF'
Usage: scripts/setup-broute-meter.sh [OPTIONS]

Install the OMK B-route systemd service and its narrowly scoped USB reset helper.

Options:
  --dry-run      Do not change anything; show resolved settings and planned operations.
  --print-unit   Print the rendered systemd unit and exit.
  -h, --help     Show this help.
EOF
}

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
warn() { log "WARN: $*"; }
fail() { log "ERROR: $*"; exit 1; }

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 || PREFLIGHT_FAILURES+=("Required command is unavailable: ${command_name}")
}

require_file() {
  local path="$1" description="$2"
  [[ -f "${path}" ]] || PREFLIGHT_FAILURES+=("${description} is missing: ${path}")
}

initialize_target_identity() {
  id "${TARGET_USER}" >/dev/null 2>&1 || fail "Target user does not exist: ${TARGET_USER}"
  TARGET_GROUP="$(id -gn "${TARGET_USER}")"
}

render_unit() {
  sed -e "s|@OMK_ROOT@|${OMK_ROOT}|g" -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|${TARGET_GROUP}|g" "${UNIT_TEMPLATE}"
}

render_sudoers() {
  printf '%s ALL=(root) NOPASSWD: %s\n' "${TARGET_USER}" "${HELPER_DEST}"
}

preflight() {
  local issue command_name
  PREFLIGHT_FAILURES=()
  PREFLIGHT_OK=true
  require_file "${UNIT_TEMPLATE}" 'Unit template'
  require_file "${HELPER_SOURCE}" 'USB reset helper'
  [[ -x "${HELPER_SOURCE}" ]] || PREFLIGHT_FAILURES+=("USB reset helper is not executable: ${HELPER_SOURCE}")
  [[ -d "${OMK_ROOT}/broute-meter" ]] || PREFLIGHT_FAILURES+=("B-route working directory is missing: ${OMK_ROOT}/broute-meter")
  for command_name in apt-get python3 install stat mktemp sed systemctl visudo cmp tee; do require_command "${command_name}"; done
  ((EUID != 0)) || require_command runuser
  if ((EUID != 0)); then
    require_command sudo
  fi

  ((${#PREFLIGHT_FAILURES[@]} == 0)) && return 0
  PREFLIGHT_OK=false
  for issue in "${PREFLIGHT_FAILURES[@]}"; do
    if "${DRY_RUN}"; then warn "A normal run would fail: ${issue}"; else fail "${issue}"; fi
  done
  return 0
}

ensure_python_runtime() {
  local package
  local -a missing=()
  for package in python3 python3-venv python3-pip; do
    dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -q '^ii' || missing+=("${package}")
  done
  if ((${#missing[@]})); then
    log "Installing direct B-route dependencies: ${missing[*]}"
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install -y "${missing[@]}"
  else
    log "Direct B-route dependencies are already installed."
  fi
  if [[ ! -d "${VENV_PATH}" ]]; then
    log "Creating B-route virtual environment: ${VENV_PATH}"
    if ((EUID == 0)); then runuser -u "${TARGET_USER}" -- python3 -m venv "${VENV_PATH}"; else "${SUDO[@]}" -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"; fi
  else
    log "B-route virtual environment exists; preserving it: ${VENV_PATH}"
  fi
  [[ -x "${VENV_PYTHON}" ]] || fail "B-route venv Python is unavailable: ${VENV_PYTHON}"
  log "Installing B-route runtime dependencies from pyproject.toml."
  if ((EUID == 0)); then
    runuser -u "${TARGET_USER}" -- "${VENV_PYTHON}" -m pip install --upgrade pip
    runuser -u "${TARGET_USER}" -- "${VENV_PYTHON}" -m pip install "${OMK_ROOT}/broute-meter"
  else
    "${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PYTHON}" -m pip install --upgrade pip
    "${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PYTHON}" -m pip install "${OMK_ROOT}/broute-meter"
  fi
}

directory_state() {
  stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "$1"
}

ensure_runtime_directory() {
  local directory="$1" ownership
  if [[ -d "${directory}" ]]; then
    ownership="$(directory_state "${directory}")"
    log "Runtime directory already exists; preserving it: ${directory} (${ownership})"
    return
  fi
  if [[ -e "${directory}" ]]; then
    fail "Runtime directory path is occupied by a non-directory: ${directory}"
  fi
  if "${DRY_RUN}"; then
    log "Would create runtime directory owned by ${TARGET_USER}:${TARGET_GROUP}: ${directory}"
    return
  fi
  log "Creating runtime directory: ${directory}"
  mkdir -p "${directory}"
  "${SUDO[@]}" chown "${TARGET_USER}:${TARGET_GROUP}" "${directory}"
}

ensure_log_directory() {
  if [[ -d "${LOG_DIR}" ]]; then
    return
  fi
  [[ ! -e "${LOG_DIR}" ]] || fail "Setup log path is occupied by a non-directory: ${LOG_DIR}"
  mkdir -p "${LOG_DIR}"
  "${SUDO[@]}" chown "${TARGET_USER}:${TARGET_GROUP}" "${LOG_DIR}"
}

ensure_credentials_permissions() {
  local credentials_path="${OMK_ROOT}/broute-meter/config/credentials.yaml" actual
  if [[ ! -e "${credentials_path}" ]]; then
    log "Credentials file is absent; preserving it as absent: ${credentials_path}"
    return
  fi
  [[ -f "${credentials_path}" ]] || fail "Credentials path is not a regular file: ${credentials_path}"
  actual="$(stat -c '%U:%G:%a' "${credentials_path}")"
  if [[ "${actual}" != "${TARGET_USER}:${TARGET_GROUP}:600" ]]; then
    log "Correcting owner/mode for credentials file without changing its contents: ${credentials_path}"
    "${SUDO[@]}" chown "${TARGET_USER}:${TARGET_GROUP}" "${credentials_path}"
    "${SUDO[@]}" chmod 0600 "${credentials_path}"
  fi
}

ensure_system_directory() {
  local directory="$1"
  if [[ -d "${directory}" ]]; then
    log "System directory already exists; preserving it: ${directory}"
  elif [[ -e "${directory}" ]]; then
    fail "System directory path is occupied by a non-directory: ${directory}"
  else
    log "Creating system directory: ${directory}"
    "${SUDO[@]}" install -d -m 0755 "${directory}"
  fi
}

ensure_root_metadata() {
  local path="$1" mode="$2" actual
  actual="$(stat -c '%u:%g:%a' "${path}")"
  if [[ "${actual}" != "0:0:${mode}" ]]; then
    log "Correcting ownership/mode for ${path}: ${actual} -> 0:0:${mode}"
    "${SUDO[@]}" chown root:root "${path}"
    "${SUDO[@]}" chmod "${mode}" "${path}"
  fi
}

compare_files() {
  local source="$1" destination="$2" status
  if cmp -s "${source}" "${destination}"; then
    return 0
  else
    status=$?
  fi
  [[ "${status}" == 1 ]] && return 1
  return 2
}

compare_sudoers() {
  local source="$1" destination="$2" status
  if "${SUDO[@]}" cmp -s "${source}" "${destination}"; then
    return 0
  else
    status=$?
  fi
  [[ "${status}" == 1 ]] && return 1
  return 2
}

plan_file_update() {
  local source="$1" destination="$2" label="$3" comparison_status
  if [[ ! -e "${destination}" ]]; then
    log "Would create ${label}: ${destination}"
  elif compare_files "${source}" "${destination}"; then
    log "Would preserve identical ${label}: ${destination}"
  else
    comparison_status=$?
    if [[ "${comparison_status}" == 1 ]]; then
      log "Would update changed ${label}: ${destination}"
    else
      warn "Cannot compare existing ${label}; dry-run will not classify it as changed: ${destination}"
    fi
  fi
}

install_helper() {
  local comparison_status
  ensure_system_directory /usr/local/lib/omk
  if [[ ! -e "${HELPER_DEST}" ]]; then
    log "Installing updated USB reset helper: ${HELPER_DEST}"
    "${SUDO[@]}" install -o root -g root -m 0755 "${HELPER_SOURCE}" "${HELPER_DEST}"
  elif compare_files "${HELPER_SOURCE}" "${HELPER_DEST}"; then
    log "USB reset helper is identical; preserving it: ${HELPER_DEST}"
  else
    comparison_status=$?
    if [[ "${comparison_status}" == 1 ]]; then
      log "Installing updated USB reset helper: ${HELPER_DEST}"
      "${SUDO[@]}" install -o root -g root -m 0755 "${HELPER_SOURCE}" "${HELPER_DEST}"
    else
      fail "Cannot compare existing USB reset helper: ${HELPER_DEST}"
    fi
  fi
  ensure_root_metadata "${HELPER_DEST}" 755
}

install_sudoers() {
  local temporary="$1" comparison_status
  ensure_system_directory /etc/sudoers.d
  render_sudoers > "${temporary}"
  "${SUDO[@]}" visudo -cf "${temporary}"
  if [[ ! -e "${SUDOERS_DEST}" ]]; then
    log "Installing updated sudoers rule: ${SUDOERS_DEST}"
    "${SUDO[@]}" install -o root -g root -m 0440 "${temporary}" "${SUDOERS_DEST}"
  elif compare_sudoers "${temporary}" "${SUDOERS_DEST}"; then
    log "Sudoers rule is identical; preserving it: ${SUDOERS_DEST}"
  else
    comparison_status=$?
    if [[ "${comparison_status}" == 1 ]]; then
      log "Installing updated sudoers rule: ${SUDOERS_DEST}"
      "${SUDO[@]}" install -o root -g root -m 0440 "${temporary}" "${SUDOERS_DEST}"
    else
      fail "Cannot compare existing sudoers rule safely: ${SUDOERS_DEST}"
    fi
  fi
  ensure_root_metadata "${SUDOERS_DEST}" 440
  "${SUDO[@]}" visudo -cf "${SUDOERS_DEST}"
}

install_unit() {
  local temporary="$1" comparison_status
  UNIT_CHANGED=false
  render_unit > "${temporary}"
  if [[ ! -e "${UNIT_DEST}" ]]; then
    log "Installing updated systemd unit: ${UNIT_DEST}"
    "${SUDO[@]}" install -o root -g root -m 0644 "${temporary}" "${UNIT_DEST}"
    UNIT_CHANGED=true
  elif compare_files "${temporary}" "${UNIT_DEST}"; then
    log "Systemd unit is identical; preserving it: ${UNIT_DEST}"
  else
    comparison_status=$?
    if [[ "${comparison_status}" == 1 ]]; then
      log "Installing updated systemd unit: ${UNIT_DEST}"
      "${SUDO[@]}" install -o root -g root -m 0644 "${temporary}" "${UNIT_DEST}"
      UNIT_CHANGED=true
    else
      fail "Cannot compare existing systemd unit: ${UNIT_DEST}"
    fi
  fi
  ensure_root_metadata "${UNIT_DEST}" 644
}

ensure_service_state() {
  if "${UNIT_CHANGED}"; then
    log "Reloading systemd after unit update."
    "${SUDO[@]}" systemctl daemon-reload
  fi
  if ! "${SUDO[@]}" systemctl is-enabled --quiet "${SERVICE}"; then
    log "Enabling ${SERVICE}."
    "${SUDO[@]}" systemctl enable "${SERVICE}"
  else
    log "Service is already enabled: ${SERVICE}"
  fi
  if "${SUDO[@]}" systemctl is-active --quiet "${SERVICE}"; then
    if "${UNIT_CHANGED}"; then
      log "Restarting active service after unit update: ${SERVICE}"
      "${SUDO[@]}" systemctl restart "${SERVICE}"
    else
      log "Service is already active; preserving it: ${SERVICE}"
    fi
  else
    log "Starting inactive service: ${SERVICE}"
    "${SUDO[@]}" systemctl start "${SERVICE}"
  fi
}

verify_installation() {
  [[ "$(stat -c '%u:%g:%a' "${HELPER_DEST}")" == '0:0:755' ]] || fail "USB reset helper metadata is incorrect: ${HELPER_DEST}"
  [[ -x "${HELPER_DEST}" ]] || fail "USB reset helper is not executable: ${HELPER_DEST}"
  [[ "$(stat -c '%u:%g:%a' "${SUDOERS_DEST}")" == '0:0:440' ]] || fail "Sudoers metadata is incorrect: ${SUDOERS_DEST}"
  "${SUDO[@]}" visudo -cf "${SUDOERS_DEST}"
  cmp -s <(render_unit) "${UNIT_DEST}" || fail "Installed unit differs from the rendered template."
  "${SUDO[@]}" systemctl is-enabled --quiet "${SERVICE}" || fail "Service is not enabled: ${SERVICE}"
  if [[ -e "${OMK_ROOT}/broute-meter/config/credentials.yaml" ]]; then
    [[ "$(stat -c '%U:%G:%a' "${OMK_ROOT}/broute-meter/config/credentials.yaml")" == "${TARGET_USER}:${TARGET_GROUP}:600" ]] ||
      fail "Credentials file owner or mode is incorrect."
  fi
  if ! "${SUDO[@]}" systemctl is-active --quiet "${SERVICE}"; then
    log "Check: sudo systemctl status ${SERVICE} --no-pager"
    log "Check: sudo journalctl -u ${SERVICE} -n 100 --no-pager"
    fail "Service is not active: ${SERVICE}"
  fi
  log "PASS: ${SERVICE} is enabled and active."
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --print-unit) PRINT_UNIT=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if "${DRY_RUN}" && "${PRINT_UNIT}"; then
  fail "--dry-run and --print-unit cannot be used together."
fi

initialize_target_identity
if "${PRINT_UNIT}"; then
  command -v sed >/dev/null 2>&1 || fail "sed is required to render the unit."
  [[ -f "${UNIT_TEMPLATE}" ]] || fail "Unit template is missing: ${UNIT_TEMPLATE}"
  render_unit
  exit 0
fi

if ((EUID != 0)); then SUDO=(sudo); fi
preflight

if "${DRY_RUN}"; then
  log "Repository root: ${OMK_ROOT}"
  log "Target user/group: ${TARGET_USER}:${TARGET_GROUP}"
  log "Service: ${SERVICE}"
  log "Would install missing python3, python3-venv, and python3-pip packages; create ${VENV_PATH} only if absent; and install runtime dependencies from broute-meter/pyproject.toml."
  if command -v stat >/dev/null 2>&1; then
    ensure_runtime_directory "${OMK_ROOT}/data/broute-meter"
    ensure_runtime_directory "${OMK_ROOT}/logs/broute-meter"
  else
    warn "Skipping runtime directory inspection because stat is unavailable."
  fi
  if [[ -d "${LOG_DIR}" ]]; then
    log "Would preserve setup log directory: ${LOG_DIR}"
  elif [[ -e "${LOG_DIR}" ]]; then
    warn "A normal run would fail: setup log path is a non-directory: ${LOG_DIR}"
  else
    log "Would create setup log directory owned by ${TARGET_USER}:${TARGET_GROUP}: ${LOG_DIR}"
  fi
  if [[ -f "${HELPER_SOURCE}" ]] && command -v cmp >/dev/null 2>&1; then
    plan_file_update "${HELPER_SOURCE}" "${HELPER_DEST}" 'USB reset helper'
  else
    warn "Skipping USB reset helper comparison because its source or cmp is unavailable."
  fi
  if [[ ! -e "${SUDOERS_DEST}" ]]; then
    log "Would create sudoers rule: ${SUDOERS_DEST}"
  elif [[ -r "${SUDOERS_DEST}" ]] && command -v cmp >/dev/null 2>&1; then
    if compare_files <(render_sudoers) "${SUDOERS_DEST}"; then
      log "Would preserve identical sudoers rule: ${SUDOERS_DEST}"
    else
      comparison_status=$?
      if [[ "${comparison_status}" == 1 ]]; then
        log "Would update changed sudoers rule: ${SUDOERS_DEST}"
      else
        warn "Cannot compare existing sudoers rule; it will not be classified as changed: ${SUDOERS_DEST}"
      fi
    fi
  else
    warn "Cannot compare existing sudoers rule without sudo in dry-run; normal execution uses privileged comparison."
  fi
  if [[ -f "${UNIT_TEMPLATE}" ]] && command -v sed >/dev/null 2>&1 && command -v cmp >/dev/null 2>&1; then
    if [[ -e "${UNIT_DEST}" ]] && compare_files <(render_unit) "${UNIT_DEST}"; then
      log "Would preserve identical systemd unit: ${UNIT_DEST}"
      log "Would enable ${SERVICE} if needed; would start it only if inactive."
    else
      comparison_status=$?
      if [[ -e "${UNIT_DEST}" && "${comparison_status}" == 2 ]]; then
        warn "Cannot compare existing systemd unit; it will not be classified as changed: ${UNIT_DEST}"
      else
        log "Would create or update systemd unit: ${UNIT_DEST}"
        log "Would run systemctl daemon-reload, enable ${SERVICE} if needed, and start or restart it according to its active state."
      fi
    fi
  else
    warn "Skipping systemd unit comparison because the template, sed, or cmp is unavailable."
  fi
  if ! "${PREFLIGHT_OK}"; then
    fail "Dry-run found prerequisite failures; no changes were made."
  fi
  exit 0
fi

ensure_log_directory
LOG_FILE="${LOG_DIR}/broute-meter-setup-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1
log "B-route meter setup started. Log file: ${LOG_FILE}"
ensure_python_runtime
ensure_runtime_directory "${OMK_ROOT}/data/broute-meter"
ensure_runtime_directory "${OMK_ROOT}/logs/broute-meter"
ensure_credentials_permissions

temporary_sudoers="$(mktemp)"
temporary_unit="$(mktemp)"
trap 'rm -f -- "${temporary_sudoers}" "${temporary_unit}"' EXIT
install_helper
install_sudoers "${temporary_sudoers}"
install_unit "${temporary_unit}"
ensure_service_state
verify_installation
log "Installed ${SERVICE}. Check: sudo systemctl status ${SERVICE} --no-pager"
