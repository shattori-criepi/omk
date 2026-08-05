#!/usr/bin/env bash

# Install the host-side Ichijo ECHONET Lite energy node on Raspberry Pi OS.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP=""
SERVICE_NAME='omk-ichijo-energy-node.service'
SERVICE_TEMPLATE="${OMK_ROOT}/systemd/omk-ichijo-energy-node.service.in"
SERVICE_DESTINATION="/etc/systemd/system/${SERVICE_NAME}"
ENV_SOURCE="${OMK_ROOT}/services/ichijo-energy-node/.env.example"
ENV_DESTINATION='/etc/omk/ichijo-energy-node.env'
REQUIREMENTS="${OMK_ROOT}/services/ichijo-energy-node/requirements.txt"
SERVICE_ROOT="${OMK_ROOT}/services/ichijo-energy-node"
VENV="${SERVICE_ROOT}/.venv"
LOG_DIR="${OMK_ROOT}/logs/setup"
DRY_RUN=false
PRINT_UNIT=false
SUDO=()
AS_TARGET=()
PREFLIGHT_OK=true
PREFLIGHT_FAILURES=()
UNIT_CHANGED=false
PIP_CHANGED=false
ENV_CREATED=false

usage() {
  cat <<'EOF'
Usage: scripts/setup-ichijo-energy-node.sh [OPTIONS]

Install the OMK Ichijo ECHONET Lite energy node as a systemd service.

Options:
  --dry-run      Do not change anything; show prerequisites and planned operations.
  --print-unit   Print the rendered systemd unit and exit.
  -h, --help     Show this help.
EOF
}

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
warn() { log "WARN: $*"; }
fail() { log "ERROR: $*"; exit 1; }

require_command() {
  local name="$1"
  command -v "${name}" >/dev/null 2>&1 || PREFLIGHT_FAILURES+=("Required command is unavailable: ${name}")
}

initialize_target() {
  id "${TARGET_USER}" >/dev/null 2>&1 || fail "Target user does not exist: ${TARGET_USER}"
  TARGET_GROUP="$(id -gn "${TARGET_USER}")"
}

render_unit() {
  sed -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|${TARGET_GROUP}|g" -e "s|@OMK_ROOT@|${OMK_ROOT}|g" "${SERVICE_TEMPLATE}"
}

preflight() {
  local command_name issue
  PREFLIGHT_FAILURES=()
  PREFLIGHT_OK=true
  [[ "$(uname -s)" == Linux ]] || PREFLIGHT_FAILURES+=('Linux is required.')
  [[ -f "${SERVICE_TEMPLATE}" ]] || PREFLIGHT_FAILURES+=("Service template is missing: ${SERVICE_TEMPLATE}")
  [[ -f "${REQUIREMENTS}" ]] || PREFLIGHT_FAILURES+=("requirements.txt is missing: ${REQUIREMENTS}")
  [[ -f "${ENV_SOURCE}" ]] || PREFLIGHT_FAILURES+=(".env.example is missing: ${ENV_SOURCE}")
  [[ -d "${SERVICE_ROOT}/src" ]] || PREFLIGHT_FAILURES+=("Service source directory is missing: ${SERVICE_ROOT}/src")
  for command_name in apt-get dpkg-query grep systemctl install mktemp sed cmp stat tee; do require_command "${command_name}"; done
  if ((EUID != 0)); then
    require_command sudo
  elif [[ "${TARGET_USER}" != root ]] && ! command -v runuser >/dev/null 2>&1 && ! command -v sudo >/dev/null 2>&1; then
    PREFLIGHT_FAILURES+=("runuser or sudo is required to run venv and pip as ${TARGET_USER}")
  fi
  if ((${#PREFLIGHT_FAILURES[@]} == 0)); then return; fi
  PREFLIGHT_OK=false
  for issue in "${PREFLIGHT_FAILURES[@]}"; do
    if "${DRY_RUN}"; then warn "A normal run would fail: ${issue}"; else fail "${issue}"; fi
  done
}

compare_files() {
  local source="$1" destination="$2" status
  if cmp -s "${source}" "${destination}"; then return 0; else status=$?; fi
  [[ "${status}" == 1 ]] && return 1
  return 2
}

directory_state() { stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "$1"; }

target_can_write_directory() {
  local path="$1"
  if ((EUID == 0)) && [[ "${TARGET_USER}" != root ]]; then
    "${AS_TARGET[@]}" test -w "${path}" -a -x "${path}"
  else
    test -w "${path}" -a -x "${path}"
  fi
}

environment_mode_has_group_or_world_permissions() {
  local mode="$1"
  (( (8#${mode} & 8#077) != 0 ))
}

plan_log_directory() {
  if [[ -d "${LOG_DIR}" ]]; then
    if target_can_write_directory "${LOG_DIR}"; then
      log "Would preserve writable setup log directory: ${LOG_DIR} ($(directory_state "${LOG_DIR}"))"
    else
      log "Would repair only setup log directory ownership/owner permissions for ${TARGET_USER}:${TARGET_GROUP}: ${LOG_DIR}"
    fi
  elif [[ -e "${LOG_DIR}" ]]; then
    fail "Setup log path is occupied by a non-directory: ${LOG_DIR}"
  else
    log "Would create setup log directory owned by ${TARGET_USER}:${TARGET_GROUP}: ${LOG_DIR}"
  fi
}

ensure_log_directory() {
  if [[ -d "${LOG_DIR}" ]]; then
    if target_can_write_directory "${LOG_DIR}"; then
      log "Setup log directory is writable; preserving it: ${LOG_DIR} ($(directory_state "${LOG_DIR}"))"
      return
    fi
    log "Repairing only setup log directory ownership/owner permissions: ${LOG_DIR}"
    "${SUDO[@]}" chown "${TARGET_USER}:${TARGET_GROUP}" "${LOG_DIR}"
    "${SUDO[@]}" chmod u+rwx "${LOG_DIR}"
  elif [[ -e "${LOG_DIR}" ]]; then
    fail "Setup log path is occupied by a non-directory: ${LOG_DIR}"
  else
    log "Creating setup log directory: ${LOG_DIR}"
    "${SUDO[@]}" install -d -o "${TARGET_USER}" -g "${TARGET_GROUP}" -m 0775 "${LOG_DIR}"
  fi
  target_can_write_directory "${LOG_DIR}" || fail "${TARGET_USER} cannot create setup logs in ${LOG_DIR} after limited directory repair."
}

ensure_system_directory() {
  local path="$1"
  if [[ -d "${path}" ]]; then
    log "System directory already exists; preserving it: ${path}"
  elif [[ -e "${path}" ]]; then
    fail "System directory path is occupied by a non-directory: ${path}"
  else
    log "Creating system directory: ${path}"
    "${SUDO[@]}" install -d -m 0755 "${path}"
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

target_can_write() {
  local path="$1"
  if ((EUID == 0)) && [[ "${TARGET_USER}" != root ]]; then
    "${AS_TARGET[@]}" test -w "${path}" -a -x "${path}"
  else
    test -w "${path}" -a -x "${path}"
  fi
}

check_packages() {
  local package
  MISSING_PACKAGES=()
  for package in python3 python3-venv python3-pip; do
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -q '^ii'; then
      MISSING_PACKAGES+=("${package}")
    fi
  done
}

ensure_packages() {
  check_packages
  if ((${#MISSING_PACKAGES[@]} == 0)); then
    log 'Required OS packages are already installed.'
  else
    log "Installing required packages: ${MISSING_PACKAGES[*]}"
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" apt-get install -y "${MISSING_PACKAGES[@]}"
  fi
  command -v python3 >/dev/null 2>&1 || fail 'python3 is unavailable after package installation.'
  python3 -m venv --help >/dev/null 2>&1 || fail 'python3 cannot create virtual environments after package installation.'
}

ensure_venv_and_dependencies() {
  if [[ -e "${VENV}" && ! -d "${VENV}" ]]; then fail "Virtual environment path is occupied by a non-directory: ${VENV}"; fi
  if [[ ! -d "${VENV}" ]]; then
    log "Creating virtual environment as ${TARGET_USER}: ${VENV}"
    "${AS_TARGET[@]}" python3 -m venv "${VENV}"
  else
    log "Virtual environment exists; preserving it: ${VENV} ($(directory_state "${VENV}"))"
  fi
  [[ -x "${VENV}/bin/python" ]] || fail "Virtual environment Python is unavailable: ${VENV}/bin/python"
  target_can_write "${VENV}" || fail "${TARGET_USER} cannot write the existing virtual environment: ${VENV}. Correct its ownership manually; this script will not use chown -R."
  log 'Installing runtime dependencies from requirements.txt (not requirements-dev.txt).'
  "${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install --upgrade pip
  "${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install -r "${REQUIREMENTS}"
  PIP_CHANGED=true
}

ensure_environment_file() {
  local mode
  ensure_system_directory /etc/omk
  if [[ -e "${ENV_DESTINATION}" ]]; then
    [[ -f "${ENV_DESTINATION}" ]] || fail "Environment file path is not a regular file: ${ENV_DESTINATION}"
    mode="$(stat -c '%a' "${ENV_DESTINATION}")"
    if environment_mode_has_group_or_world_permissions "${mode}"; then warn "Existing environment file has group/world permissions (${mode}); preserving it without reading or changing it."; fi
    log "Environment file exists; preserving it without reading: ${ENV_DESTINATION}"
    return
  fi
  log "Creating environment file from example with mode 0600: ${ENV_DESTINATION}"
  "${SUDO[@]}" install -o root -g root -m 0600 "${ENV_SOURCE}" "${ENV_DESTINATION}"
  ENV_CREATED=true
}

install_unit() {
  local temporary="$1" comparison_status
  render_unit > "${temporary}"
  UNIT_CHANGED=false
  if [[ ! -e "${SERVICE_DESTINATION}" ]]; then
    log "Installing new systemd unit: ${SERVICE_DESTINATION}"
    "${SUDO[@]}" install -o root -g root -m 0644 "${temporary}" "${SERVICE_DESTINATION}"
    UNIT_CHANGED=true
  elif compare_files "${temporary}" "${SERVICE_DESTINATION}"; then
    log "Systemd unit is identical; preserving it: ${SERVICE_DESTINATION}"
  else
    comparison_status=$?
    if [[ "${comparison_status}" == 1 ]]; then
      log "Installing updated systemd unit: ${SERVICE_DESTINATION}"
      "${SUDO[@]}" install -o root -g root -m 0644 "${temporary}" "${SERVICE_DESTINATION}"
      UNIT_CHANGED=true
    else
      fail "Cannot compare existing systemd unit safely: ${SERVICE_DESTINATION}"
    fi
  fi
  ensure_root_metadata "${SERVICE_DESTINATION}" 644
}

ensure_service_state() {
  local restart_needed=false
  if "${UNIT_CHANGED}"; then
    log 'Reloading systemd after unit update.'
    "${SUDO[@]}" systemctl daemon-reload
  fi
  if ! "${SUDO[@]}" systemctl is-enabled --quiet "${SERVICE_NAME}"; then
    log "Enabling ${SERVICE_NAME}."
    "${SUDO[@]}" systemctl enable "${SERVICE_NAME}"
  else
    log "Service is already enabled: ${SERVICE_NAME}"
  fi
  if "${UNIT_CHANGED}" || "${PIP_CHANGED}" || "${ENV_CREATED}"; then
    restart_needed=true
  fi
  if "${SUDO[@]}" systemctl is-active --quiet "${SERVICE_NAME}"; then
    if "${restart_needed}"; then
      log "Restarting active service after setup changes: ${SERVICE_NAME}"
      "${SUDO[@]}" systemctl restart "${SERVICE_NAME}"
    else
      log "Service is already active; preserving it: ${SERVICE_NAME}"
    fi
  else
    log "Starting inactive service: ${SERVICE_NAME}"
    "${SUDO[@]}" systemctl start "${SERVICE_NAME}"
  fi
}

verify_installation() {
  [[ -x "${VENV}/bin/python" ]] || fail 'Virtual environment Python is not executable after setup.'
  [[ -f "${ENV_DESTINATION}" ]] || fail "Environment file is missing after setup: ${ENV_DESTINATION}"
  [[ "$(stat -c '%u:%g:%a' "${SERVICE_DESTINATION}")" == '0:0:644' ]] || fail "Unit metadata is incorrect: ${SERVICE_DESTINATION}"
  compare_files <(render_unit) "${SERVICE_DESTINATION}" || fail "Installed unit differs from the rendered template."
  "${SUDO[@]}" systemctl is-enabled --quiet "${SERVICE_NAME}" || fail "Service is not enabled: ${SERVICE_NAME}"
  if ! "${SUDO[@]}" systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "Check: sudo systemctl status ${SERVICE_NAME} --no-pager"
    log "Check: sudo journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
    fail "Service is not active: ${SERVICE_NAME}"
  fi
  log "PASS: ${SERVICE_NAME} is enabled and active."
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
if "${DRY_RUN}" && "${PRINT_UNIT}"; then fail '--dry-run and --print-unit cannot be used together.'; fi

initialize_target
if "${PRINT_UNIT}"; then
  command -v sed >/dev/null 2>&1 || fail 'sed is required to render the unit.'
  [[ -f "${SERVICE_TEMPLATE}" ]] || fail "Service template is missing: ${SERVICE_TEMPLATE}"
  render_unit
  exit 0
fi

if ((EUID == 0)) && [[ "${TARGET_USER}" != root ]]; then
  if command -v runuser >/dev/null 2>&1; then AS_TARGET=(runuser -u "${TARGET_USER}" --); else AS_TARGET=(sudo -u "${TARGET_USER}"); fi
elif ((EUID != 0)); then
  SUDO=(sudo)
fi
preflight

if "${DRY_RUN}"; then
  log "Repository root: ${OMK_ROOT}"
  log "Target user/group: ${TARGET_USER}:${TARGET_GROUP}"
  plan_log_directory
  if command -v dpkg-query >/dev/null 2>&1 && command -v grep >/dev/null 2>&1; then
    check_packages
    if ((${#MISSING_PACKAGES[@]})); then
      log "Would install OS packages: ${MISSING_PACKAGES[*]}"
    else
      log 'OS packages are already installed; no apt operation planned.'
    fi
  else
    MISSING_PACKAGES=()
    warn 'Skipping OS package inspection because dpkg-query or grep is unavailable.'
  fi
  if [[ -d "${VENV}" ]]; then
    log "Would preserve virtual environment: ${VENV}"
  elif [[ -e "${VENV}" ]]; then
    warn "A normal run would fail: venv path is a non-directory: ${VENV}"
  elif ((${#MISSING_PACKAGES[@]})) && [[ " ${MISSING_PACKAGES[*]} " == *' python3 '* ]]; then
    log "Would create virtual environment after package installation: ${VENV}"
  else
    log "Would create virtual environment as ${TARGET_USER}: ${VENV}"
  fi
  if [[ -f "${REQUIREMENTS}" ]]; then
    log "Would install runtime dependencies after the virtual environment is available and restart an active service."
  else
    warn 'Skipping pip plan because requirements.txt is unavailable.'
  fi
  if [[ -e "${ENV_DESTINATION}" ]]; then log "Would preserve existing environment file without reading it: ${ENV_DESTINATION}"; elif [[ -f "${ENV_SOURCE}" ]]; then log "Would create environment file from example with mode 0600: ${ENV_DESTINATION}"; else warn 'Skipping environment-file plan because .env.example is unavailable.'; fi
  if [[ -f "${SERVICE_TEMPLATE}" ]] && command -v sed >/dev/null 2>&1 && command -v cmp >/dev/null 2>&1; then
    if [[ ! -e "${SERVICE_DESTINATION}" ]]; then
      log "Would create systemd unit: ${SERVICE_DESTINATION}"
    elif compare_files <(render_unit) "${SERVICE_DESTINATION}"; then
      log "Would preserve identical systemd unit: ${SERVICE_DESTINATION}"
    else
      comparison_status=$?
      if [[ "${comparison_status}" == 1 ]]; then
        log "Would update changed systemd unit: ${SERVICE_DESTINATION}"
      else
        warn "Cannot compare existing systemd unit; it will not be classified as changed: ${SERVICE_DESTINATION}"
      fi
    fi
    log "Would daemon-reload only if the unit changes, enable if needed, and start or restart according to service state."
  else
    warn 'Skipping systemd unit comparison because the template, sed, or cmp is unavailable.'
  fi
  if ! "${PREFLIGHT_OK}"; then fail 'Dry-run found prerequisite failures; no changes were made.'; fi
  exit 0
fi

ensure_log_directory
LOG_FILE="${LOG_DIR}/ichijo-energy-node-setup-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1
log "Ichijo energy node setup started. Log file: ${LOG_FILE}"
ensure_packages
ensure_venv_and_dependencies
ensure_environment_file
temporary_unit="$(mktemp)"
trap 'rm -f -- "${temporary_unit}"' EXIT
install_unit "${temporary_unit}"
ensure_service_state
verify_installation
log "Installed ${SERVICE_NAME}. Check: sudo systemctl status ${SERVICE_NAME} --no-pager"
