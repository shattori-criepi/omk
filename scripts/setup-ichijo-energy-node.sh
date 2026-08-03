#!/usr/bin/env bash

# Install the host-side Ichijo ECHONET Lite energy node on Raspberry Pi OS.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
SERVICE_NAME="omk-ichijo-energy-node.service"
SERVICE_TEMPLATE="${OMK_ROOT}/systemd/omk-ichijo-energy-node.service.in"
SERVICE_DESTINATION="/etc/systemd/system/${SERVICE_NAME}"
ENV_DESTINATION="/etc/omk/ichijo-energy-node.env"
VENV="${OMK_ROOT}/services/ichijo-energy-node/.venv"
PRINT_UNIT=false
SUDO=()
AS_TARGET=()

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

usage() {
  cat <<'EOF'
Usage: scripts/setup-ichijo-energy-node.sh [--print-unit]

Installs the OMK Ichijo ECHONET Lite energy node as a systemd service.
--print-unit renders the service template without writing files or using sudo.
EOF
}

if (($# > 0)); then
  case "$1" in
    --print-unit) PRINT_UNIT=true ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
fi

render_unit() {
  sed \
    -e "s|@OMK_USER@|${TARGET_USER}|g" \
    -e "s|@OMK_GROUP@|${TARGET_GROUP}|g" \
    -e "s|@OMK_ROOT@|${OMK_ROOT}|g" \
    "${SERVICE_TEMPLATE}"
}

if [[ "${PRINT_UNIT}" == true ]]; then
  render_unit
  exit 0
fi

if [[ "$(uname -s)" != Linux ]]; then
  log "ERROR: Linux is required."
  exit 1
fi
if ! id "${TARGET_USER}" >/dev/null 2>&1; then
  log "ERROR: Target user does not exist: ${TARGET_USER}"
  exit 1
fi
if [[ ! -f "${SERVICE_TEMPLATE}" || ! -f "${OMK_ROOT}/services/ichijo-energy-node/requirements.txt" || ! -f "${OMK_ROOT}/services/ichijo-energy-node/.env.example" ]]; then
  log "ERROR: Could not identify the OMK repository root or Ichijo energy node files."
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
  log "ERROR: apt-get and systemctl are required."
  exit 1
fi

if ((EUID == 0)); then
  if [[ "${TARGET_USER}" != root ]]; then
    if command -v runuser >/dev/null 2>&1; then
      AS_TARGET=(runuser -u "${TARGET_USER}" --)
    elif command -v sudo >/dev/null 2>&1; then
      AS_TARGET=(sudo -u "${TARGET_USER}")
    else
      log "ERROR: runuser or sudo is required to create the virtual environment as ${TARGET_USER}."
      exit 1
    fi
  fi
else
  if ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: sudo is required when not run as root."
    exit 1
  fi
  SUDO=(sudo)
  "${SUDO[@]}" -v
fi

LOG_DIR="${OMK_ROOT}/logs/setup"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/ichijo-energy-node-setup-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log "OMK Ichijo energy node setup started."
log "Repository root: ${OMK_ROOT}"
log "Target user/group: ${TARGET_USER}:${TARGET_GROUP}"

REQUIRED_PACKAGES=(python3 python3-venv python3-pip)
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
if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 is unavailable after package installation."
  exit 1
fi

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
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install -r "${OMK_ROOT}/services/ichijo-energy-node/requirements.txt"

"${SUDO[@]}" install -d -m 0755 /etc/omk
if [[ -e "${ENV_DESTINATION}" ]]; then
  log "Environment file exists; preserving it: ${ENV_DESTINATION}"
else
  "${SUDO[@]}" install -o root -g root -m 0644 "${OMK_ROOT}/services/ichijo-energy-node/.env.example" "${ENV_DESTINATION}"
  log "Created environment file: ${ENV_DESTINATION}"
fi

TEMP_UNIT="$(mktemp)"
trap 'rm -f -- "${TEMP_UNIT}"' EXIT
render_unit >"${TEMP_UNIT}"
if [[ -f "${SERVICE_DESTINATION}" ]] && cmp -s "${TEMP_UNIT}" "${SERVICE_DESTINATION}"; then
  log "Unit is unchanged; preserving it: ${SERVICE_DESTINATION}"
else
  if [[ -e "${SERVICE_DESTINATION}" ]]; then
    BACKUP="${SERVICE_DESTINATION}.bak.$(date '+%Y%m%d-%H%M%S')"
    "${SUDO[@]}" cp -a "${SERVICE_DESTINATION}" "${BACKUP}"
    log "Backed up existing unit to: ${BACKUP}"
  fi
  "${SUDO[@]}" install -m 0644 "${TEMP_UNIT}" "${SERVICE_DESTINATION}"
  log "Installed unit: ${SERVICE_DESTINATION}"
fi

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now "${SERVICE_NAME}"

log "Setup completed. Verify with:"
log "  sudo systemctl status ${SERVICE_NAME} --no-pager"
log "  systemctl is-enabled ${SERVICE_NAME}"
log "  systemctl is-active ${SERVICE_NAME}"
log "  sudo journalctl -u ${SERVICE_NAME} -f"
log "Stop and disable with: sudo systemctl disable --now ${SERVICE_NAME}"
