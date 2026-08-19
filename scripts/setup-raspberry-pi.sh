#!/usr/bin/env bash

# Prepare a 64-bit Raspberry Pi OS host for running OMK with Docker.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
CURRENT_USER="${SUDO_USER:-$(id -un)}"
LOG_DIR="${OMK_ROOT}/logs/setup"
RUN_STARTED_AT="$(date --iso-8601=seconds)"
LOG_FILE=""
DOCKER_VERSION="not checked"
COMPOSE_VERSION="not checked"
REBOOT_REQUIRED="no"
RELOGIN_REQUIRED="no"
REBOOT_REQUIRED_FILE="/var/run/reboot-required"
KERNEL_MODULES_DIR="/lib/modules"

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

in_group() {
  local user="$1"
  local wanted_group="$2"
  local group

  while IFS= read -r group; do
    if [[ "${group}" == "${wanted_group}" ]]; then
      return 0
    fi
  done < <(id -nG "${user}" | tr ' ' '\n')

  return 1
}

latest_installed_kernel_release() {
  local module_dir

  [[ -d "${KERNEL_MODULES_DIR}" ]] || return 0
  for module_dir in "${KERNEL_MODULES_DIR}"/*; do
    [[ -d "${module_dir}/kernel" ]] || continue
    # A kernel module file avoids treating an empty or partial directory as an
    # installed bootable kernel.
    find "${module_dir}/kernel" -type f -print -quit 2>/dev/null | grep -q . || continue
    basename "${module_dir}"
  done | sort -V | tail -n 1
}

reboot_is_required() {
  local current_kernel latest_kernel

  [[ -e "${REBOOT_REQUIRED_FILE}" ]] && return 0
  current_kernel="$(uname -r)"
  latest_kernel="$(latest_installed_kernel_release)"
  [[ -n "${latest_kernel}" && "${latest_kernel}" != "${current_kernel}" ]] || return 1
  # Do not flag a machine merely because it retains an older installed kernel.
  [[ "$(printf '%s\n%s\n' "${current_kernel}" "${latest_kernel}" | sort -V | tail -n 1)" == "${latest_kernel}" ]]
}

package_is_installed() {
  local package_status

  package_status="$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null || true)"
  [[ "${package_status}" == ii* ]]
}

finish() {
  local exit_status=$?

  if reboot_is_required; then
    REBOOT_REQUIRED="yes"
  fi

  if ((exit_status == 0)); then
    log "Setup result: SUCCESS"
  else
    log "Setup result: FAILURE (exit status: ${exit_status})"
  fi
  log "Docker Engine: ${DOCKER_VERSION}"
  log "Docker Compose: ${COMPOSE_VERSION}"
  log "Reboot required: ${REBOOT_REQUIRED}"
  log "Re-login required for Docker group: ${RELOGIN_REQUIRED}"
  if [[ -n "${LOG_FILE}" ]]; then
    log "Log file: ${LOG_FILE}"
  fi
}

trap finish EXIT

# Keep setup logs outside Git while retaining them with other OMK runtime logs.
# A newly created log directory belongs to the target user; existing ownership
# (including container-oriented ownership elsewhere in the runtime tree) is preserved.
if [[ ! -d "${LOG_DIR}" ]]; then
  mkdir -p "${LOG_DIR}"
  if id "${CURRENT_USER}" >/dev/null 2>&1; then
    chown "${CURRENT_USER}:$(id -gn "${CURRENT_USER}")" "${LOG_DIR}"
  fi
fi
LOG_FILE="${LOG_DIR}/setup-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log "OMK Raspberry Pi setup started at ${RUN_STARTED_AT}"
log "Repository root: ${OMK_ROOT}"
log "Target user: ${CURRENT_USER}"

# Validate the host before making package or account changes.
if [[ "$(uname -s)" != "Linux" ]]; then
  log "ERROR: This script supports Linux only."
  exit 1
fi

ARCH="$(uname -m)"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "arm64" ]]; then
  log "ERROR: A 64-bit ARM environment is required (detected: ${ARCH})."
  exit 1
fi

if [[ -r /proc/device-tree/model ]] &&
  grep -aq "Raspberry Pi" /proc/device-tree/model; then
  PI_MODEL="$(tr -d '\0' </proc/device-tree/model)"
  log "Detected hardware: ${PI_MODEL}"
else
  log "WARNING: Raspberry Pi model was not detected; continuing on ARM64 Linux."
fi

if ! id "${CURRENT_USER}" >/dev/null 2>&1; then
  log "ERROR: Target user does not exist: ${CURRENT_USER}"
  exit 1
fi

if [[ "${CURRENT_USER}" != "omkdev" ]]; then
  log "WARNING: Standard OMK user is omkdev; continuing for ${CURRENT_USER}."
fi

if [[ ! -f "${OMK_ROOT}/scripts/setup-raspberry-pi.sh" ]]; then
  log "ERROR: Could not identify the OMK repository root from the script location."
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  log "ERROR: apt-get is required (Raspberry Pi OS or Debian is expected)."
  exit 1
fi

if ((EUID == 0)); then
  SUDO=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: sudo is required when the script is not run as root."
    exit 1
  fi
  SUDO=(sudo)
  "${SUDO[@]}" -v
fi

# Update Raspberry Pi OS and install reusable command-line tools.
log "Updating OS package indexes."
"${SUDO[@]}" apt-get update
log "Applying available OS package upgrades."
"${SUDO[@]}" apt-get full-upgrade -y

BASE_PACKAGES=(
  ca-certificates
  curl
  git
  htop
  jq
  nano
  tree
  unzip
  vim
)
log "Installing base packages: ${BASE_PACKAGES[*]}"
"${SUDO[@]}" apt-get install -y "${BASE_PACKAGES[@]}"

# Install Docker from its official Debian repository only when components are missing.
if command -v docker >/dev/null 2>&1 &&
  docker compose version >/dev/null 2>&1; then
  log "Docker Engine and Compose plugin are already available; skipping installation."
else
  # shellcheck disable=SC1091
  source /etc/os-release
  DOCKER_CODENAME="${VERSION_CODENAME:-}"
  if [[ -z "${DOCKER_CODENAME}" ]]; then
    log "ERROR: VERSION_CODENAME is missing from /etc/os-release."
    exit 1
  fi

  # Do not silently replace an existing, differently packaged Docker runtime.
  CONFLICTING_PACKAGES=(
    containerd
    docker-buildx
    docker-compose
    docker-doc
    docker.io
    podman-docker
    runc
  )
  INSTALLED_CONFLICTS=()
  for package in "${CONFLICTING_PACKAGES[@]}"; do
    if package_is_installed "${package}"; then
      INSTALLED_CONFLICTS+=("${package}")
    fi
  done
  if ((${#INSTALLED_CONFLICTS[@]} > 0)); then
    log "ERROR: Packages conflict with Docker's official packages: ${INSTALLED_CONFLICTS[*]}"
    log "Review and migrate the existing Docker installation manually, then rerun this script."
    exit 1
  fi

  log "Configuring the official Docker apt repository for ${DOCKER_CODENAME}."
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    DOCKER_GPG_TEMP="$(mktemp)"
    curl -fsSL https://download.docker.com/linux/debian/gpg -o "${DOCKER_GPG_TEMP}"
    "${SUDO[@]}" install -m 0644 "${DOCKER_GPG_TEMP}" /etc/apt/keyrings/docker.asc
    rm -f -- "${DOCKER_GPG_TEMP}"
  fi
  "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.asc

  if [[ ! -f /etc/apt/sources.list.d/docker.sources ]]; then
    {
      printf '%s\n' \
        'Types: deb' \
        'URIs: https://download.docker.com/linux/debian' \
        "Suites: ${DOCKER_CODENAME}" \
        'Components: stable' \
        "Architectures: $(dpkg --print-architecture)" \
        'Signed-By: /etc/apt/keyrings/docker.asc'
    } | "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.sources >/dev/null
  fi

  "${SUDO[@]}" apt-get update
  DOCKER_PACKAGES=(
    containerd.io
    docker-buildx-plugin
    docker-ce
    docker-ce-cli
    docker-compose-plugin
  )
  log "Installing Docker packages: ${DOCKER_PACKAGES[*]}"
  "${SUDO[@]}" apt-get install -y "${DOCKER_PACKAGES[@]}"
fi

if command -v systemctl >/dev/null 2>&1; then
  log "Enabling and starting the Docker service."
  "${SUDO[@]}" systemctl enable --now docker
fi

if ! command -v docker >/dev/null 2>&1; then
  log "ERROR: Docker Engine is unavailable after installation."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  log "ERROR: Docker Compose plugin is unavailable after installation."
  exit 1
fi
DOCKER_VERSION="$(docker --version)"
COMPOSE_VERSION="$(docker compose version)"
log "Verified ${DOCKER_VERSION}"
log "Verified ${COMPOSE_VERSION}"

# Grant the target user Docker access without duplicating group membership.
if in_group "${CURRENT_USER}" docker; then
  log "User ${CURRENT_USER} already belongs to the docker group."
else
  log "Adding ${CURRENT_USER} to the docker group."
  "${SUDO[@]}" usermod -aG docker "${CURRENT_USER}"
  RELOGIN_REQUIRED="yes"
fi

# Create only missing standard runtime directories. Service-specific setup scripts
# re-check their own paths; neither script changes existing data or ownership.
RUNTIME_DIRECTORIES=(
  "${OMK_ROOT}/data"
  "${OMK_ROOT}/data/broute-meter"
  "${OMK_ROOT}/data/sensors"
  "${OMK_ROOT}/data/latest"
  "${OMK_ROOT}/data/processed"
  "${OMK_ROOT}/data/errors/transform"
  "${OMK_ROOT}/data/harvest-uploader"
  "${OMK_ROOT}/logs/broute-meter"
  "${OMK_ROOT}/logs/setup"
  "${OMK_ROOT}/services/mosquitto/data"
)
for directory in "${RUNTIME_DIRECTORIES[@]}"; do
  if [[ -d "${directory}" ]]; then
    log "Runtime directory already exists; preserving it: ${directory} ($(stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "${directory}"))"
  elif [[ -e "${directory}" ]]; then
    log "ERROR: Required directory path is occupied by a non-directory: ${directory}"
    exit 1
  else
    log "Creating runtime directory: ${directory}"
    mkdir -p "${directory}"
    "${SUDO[@]}" chown "${CURRENT_USER}:$(id -gn "${CURRENT_USER}")" "${directory}"
  fi
done

if reboot_is_required; then
  REBOOT_REQUIRED="yes"
  latest_kernel="$(latest_installed_kernel_release)"
  if [[ -n "${latest_kernel}" && "${latest_kernel}" != "$(uname -r)" ]]; then
    log "A reboot is required: running kernel $(uname -r) differs from latest installed kernel ${latest_kernel}; it will not be started automatically."
  else
    log "A reboot is required by installed OS updates; it will not be started automatically."
  fi
fi
if [[ "${RELOGIN_REQUIRED}" == "yes" ]]; then
  log "Log out and back in (or reboot) before using Docker without sudo."
fi

log "OMK Raspberry Pi setup completed."
log "Optional next step for a connected SORACOM Onyx: ./scripts/setup-soracom-onyx.sh"
