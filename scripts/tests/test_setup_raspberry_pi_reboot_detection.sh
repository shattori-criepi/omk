#!/usr/bin/env bash

# Exercise reboot detection without running the host setup script.
set -euo pipefail

SETUP="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-raspberry-pi.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# Load only the helpers under test; avoid the setup script's main flow.
# shellcheck disable=SC1090
source <(sed -n '/^latest_installed_kernel_release()/,/^package_is_installed()/p' "${SETUP}" | sed '$d')

KERNEL_MODULES_DIR="${TEMP_DIR}/modules"
REBOOT_REQUIRED_FILE="${TEMP_DIR}/reboot-required"
mkdir -p "${KERNEL_MODULES_DIR}/6.18.34+rpt-rpi-v8/kernel" "${KERNEL_MODULES_DIR}/6.18.39+rpt-rpi-v8/kernel"
touch "${KERNEL_MODULES_DIR}/6.18.34+rpt-rpi-v8/kernel/test.ko" "${KERNEL_MODULES_DIR}/6.18.39+rpt-rpi-v8/kernel/test.ko"
uname() { printf '%s\n' '6.18.34+rpt-rpi-v8'; }

reboot_is_required

rm -rf "${KERNEL_MODULES_DIR}/6.18.39+rpt-rpi-v8"
! reboot_is_required

# A newer kernel for a different board and an empty partial install must not
# trap Pi 4 in a reboot loop, including the Gateway continuation check.
mkdir -p "${KERNEL_MODULES_DIR}/6.99.0+rpt-rpi-2712/kernel" "${KERNEL_MODULES_DIR}/6.99.0+rpt-rpi-v8/kernel"
touch "${KERNEL_MODULES_DIR}/6.99.0+rpt-rpi-2712/kernel/test.ko"
! reboot_is_required
GATEWAY="$(dirname "${SETUP}")/setup-omk-gateway.sh"
source <(sed -n '/^base_reboot_is_required()/,/^}$/p' "${GATEWAY}")
OMK_KERNEL_MODULES_DIR="${KERNEL_MODULES_DIR}"
OMK_REBOOT_REQUIRED_PATH="${REBOOT_REQUIRED_FILE}"
! base_reboot_is_required
touch "${KERNEL_MODULES_DIR}/6.99.0+rpt-rpi-v8/kernel/test.ko"
reboot_is_required
base_reboot_is_required

touch "${REBOOT_REQUIRED_FILE}"
reboot_is_required
base_reboot_is_required

echo 'PASS: reboot detection covers the standard marker and a newer installed kernel.'
