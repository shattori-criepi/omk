#!/usr/bin/env bash

# Install the host-side OMK data-transformer timer on Raspberry Pi OS.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
LOG_DIR="${OMK_ROOT}/logs/setup"
PRINT_UNITS=false
SUDO=()
AS_TARGET=()

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

usage() {
  cat <<'EOF'
Usage: scripts/setup-data-transformer.sh [--print-units]

Installs the OMK data-transformer systemd service and hourly timer.
--print-units renders the service template to standard output without writing /etc.
EOF
}

if (($# > 0)); then
  case "$1" in
    --print-units) PRINT_UNITS=true ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
fi

render_service() {
  sed \
    -e "s|@OMK_USER@|${TARGET_USER}|g" \
    -e "s|@OMK_GROUP@|${TARGET_GROUP}|g" \
    -e "s|@OMK_ROOT@|${OMK_ROOT}|g" \
    "${OMK_ROOT}/systemd/omk-data-transformer.service.in"
}

if [[ "${PRINT_UNITS}" == true ]]; then
  render_service
  printf '\n# omk-data-transformer.timer\n'
  cat "${OMK_ROOT}/systemd/omk-data-transformer.timer"
  exit 0
fi

if [[ "$(uname -s)" != Linux ]]; then
  log "ERROR: Linux is required."
  exit 1
fi
if [[ "$(uname -m)" != aarch64 && "$(uname -m)" != arm64 ]]; then
  log "ERROR: 64-bit ARM is required (detected: $(uname -m))."
  exit 1
fi
if ! id "${TARGET_USER}" >/dev/null 2>&1; then
  log "ERROR: Target user does not exist: ${TARGET_USER}"
  exit 1
fi
if [[ ! -f "${OMK_ROOT}/services/data-transformer/requirements.txt" ]]; then
  log "ERROR: Could not identify the OMK repository root."
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
  log "ERROR: apt-get and systemctl are required."
  exit 1
fi

if ((EUID == 0)); then
  AS_TARGET=(sudo -u "${TARGET_USER}")
else
  if ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: sudo is required when not run as root."
    exit 1
  fi
  SUDO=(sudo)
  "${SUDO[@]}" -v
fi

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/data-transformer-setup-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log "OMK data-transformer setup started."
log "Repository root: ${OMK_ROOT}"
log "Target user/group: ${TARGET_USER}:${TARGET_GROUP}"

REQUIRED_PACKAGES=(python3 python3-venv python3-pip util-linux)
MISSING_PACKAGES=()
for package in "${REQUIRED_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -q '^ii'; then
    MISSING_PACKAGES+=("${package}")
  fi
done
if ((${#MISSING_PACKAGES[@]} > 0)); then
  log "Installing required packages: ${MISSING_PACKAGES[*]}"
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y "${MISSING_PACKAGES[@]}"
else
  log "Required OS packages are already installed."
fi

ensure_directory() {
  local directory="$1"
  if [[ -d "${directory}" ]]; then
    log "Directory exists; preserving it: ${directory}"
  elif [[ -e "${directory}" ]]; then
    log "ERROR: Required directory is occupied by a non-directory: ${directory}"
    exit 1
  else
    "${SUDO[@]}" install -d -o "${TARGET_USER}" -g "${TARGET_GROUP}" "${directory}"
    log "Created directory: ${directory}"
  fi
}

ensure_directory "${OMK_ROOT}/data/processed"
ensure_directory "${OMK_ROOT}/data/errors/transform"

VENV="${OMK_ROOT}/services/data-transformer/.venv"
if [[ ! -d "${VENV}" ]]; then
  log "Creating virtual environment: ${VENV}"
  "${AS_TARGET[@]}" python3 -m venv "${VENV}"
else
  log "Virtual environment exists; preserving it: ${VENV}"
fi
if [[ ! -x "${VENV}/bin/python" ]]; then
  log "ERROR: Virtual environment Python is unavailable: ${VENV}/bin/python"
  exit 1
fi
log "Installing runtime dependencies from requirements.txt (not requirements-dev.txt)."
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install --upgrade pip
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install -r "${OMK_ROOT}/services/data-transformer/requirements.txt"

install_unit() {
  local source="$1"
  local destination="$2"
  if [[ -f "${destination}" ]] && cmp -s "${source}" "${destination}"; then
    log "Unit is unchanged; preserving it: ${destination}"
    return
  fi
  if [[ -e "${destination}" ]]; then
    backup="${destination}.bak.$(date '+%Y%m%d-%H%M%S')"
    "${SUDO[@]}" cp -a "${destination}" "${backup}"
    log "Backed up existing unit to: ${backup}"
  fi
  "${SUDO[@]}" install -m 0644 "${source}" "${destination}"
  log "Installed unit: ${destination}"
}

TEMP_SERVICE="$(mktemp)"
trap 'rm -f -- "${TEMP_SERVICE}"' EXIT
render_service >"${TEMP_SERVICE}"
install_unit "${TEMP_SERVICE}" /etc/systemd/system/omk-data-transformer.service
install_unit "${OMK_ROOT}/systemd/omk-data-transformer.timer" /etc/systemd/system/omk-data-transformer.timer

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now omk-data-transformer.timer

log "Setup completed. Verify with:"
log "  sudo systemctl status omk-data-transformer.timer --no-pager"
log "  systemctl list-timers omk-data-transformer.timer"
log "  sudo journalctl -u omk-data-transformer.service --since today --no-pager"
log "Stop and disable with: sudo systemctl disable --now omk-data-transformer.timer"
