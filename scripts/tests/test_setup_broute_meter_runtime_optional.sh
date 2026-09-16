#!/usr/bin/env bash

# Verify that installation is successful even when B-route runtime prerequisites
# are absent, while broken installed artifacts remain fatal.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT}/scripts/setup-broute-meter.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# shellcheck source=/dev/null
source "${SETUP}"

SUDO=()
TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
OMK_ROOT="${TEMP_DIR}/omk"
SETTINGS_PATH="${OMK_ROOT}/services/broute-meter/config/settings.yaml"
SETTINGS_EXAMPLE="${ROOT}/services/broute-meter/config/settings.example.yaml"
HELPER_DEST="${TEMP_DIR}/reset-rs-wsuha-p-usb"
VBUS_HELPER_DEST="${TEMP_DIR}/cycle-gateway-usb-vbus"
SUDOERS_DEST="${TEMP_DIR}/omk-rs-wsuha-p-reset"
UNIT_DEST="${TEMP_DIR}/omk-broute-meter.service"
SERVICE_ACTIVE=false
START_RESULT=1
UNIT_CHANGED=false
SYSTEMCTL_CALLS=()
SYSTEMD_STATE=$'active\nrunning\nsuccess'

mkdir -p "${OMK_ROOT}/services/broute-meter/config"
cp "${HELPER_SOURCE}" "${HELPER_DEST}"
cp "${VBUS_HELPER_SOURCE}" "${VBUS_HELPER_DEST}"
chmod 0755 "${HELPER_DEST}" "${VBUS_HELPER_DEST}"
printf '%s\n' "$(render_sudoers)" >"${SUDOERS_DEST}"
render_unit >"${UNIT_DEST}"

systemctl() {
  SYSTEMCTL_CALLS+=("$*")
  case "$1" in
    is-enabled) return 0 ;;
    is-active) "${SERVICE_ACTIVE}" ;;
    start|restart|stop) return "${START_RESULT}" ;;
    show) printf '%s\n' "${SYSTEMD_STATE}" ;;
    daemon-reload) return 0 ;;
    *) return 0 ;;
  esac
}
visudo() { return 0; }
stat() {
  if [[ "$1" == '-c' && "$2" == '%u:%g:%a' ]]; then
    case "$3" in
      "${HELPER_DEST}"|"${VBUS_HELPER_DEST}") printf '0:0:755\n' ;;
      "${SUDOERS_DEST}") printf '0:0:440\n' ;;
    esac
    return 0
  fi
  command stat "$@"
}

# A fresh setup without a confirmed adapter enables the unit but never starts
# an unconfigured runtime that would otherwise fail its port detection.
SYSTEMCTL_CALLS=()
ensure_service_state
[[ " ${SYSTEMCTL_CALLS[*]} " != *" start ${SERVICE} "* ]]
[[ " ${SYSTEMCTL_CALLS[*]} " != *" restart ${SERVICE} "* ]]
verify_installation

# Once setup has a stable explicit port, normal start/restart behavior applies.
cat >"${SETTINGS_PATH}" <<'EOF'
serial:
  port: "/dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_fixture-if00-port0"
EOF

# An immediately exiting process must not produce the active PASS message.
SYSTEMD_STATE=$'activating\nauto-restart\nexit-code'
unstable_output="$(verify_installation)"
[[ "${unstable_output}" == *'enabled but not active'* ]]
[[ "${unstable_output}" != *'enabled and healthy'* ]]
SYSTEMD_STATE=$'active\nrunning\nsuccess'

# Every normal setup installs the B-route package, so an active service must
# restart even when its unit is unchanged.
SERVICE_ACTIVE=true
START_RESULT=0
UNIT_CHANGED=false
SYSTEMCTL_CALLS=()
ensure_service_state
[[ " ${SYSTEMCTL_CALLS[*]} " == *" restart ${SERVICE} "* ]]
[[ " ${SYSTEMCTL_CALLS[*]} " != *" daemon-reload "* ]]
verify_installation

# A normal systemd start still initializing the adapter is healthy, unlike
# auto-restart/exit-code and must not be reported as a startup failure.
SYSTEMD_STATE=$'activating\nstart\nsuccess'
initializing_output="$(verify_installation)"
[[ "${initializing_output}" == *'enabled and healthy'* ]]
SYSTEMD_STATE=$'active\nrunning\nsuccess'

# A changed unit still reloads systemd before restarting the active service.
UNIT_CHANGED=true
SYSTEMCTL_CALLS=()
ensure_service_state
[[ " ${SYSTEMCTL_CALLS[*]} " == *" daemon-reload "* ]]
[[ " ${SYSTEMCTL_CALLS[*]} " == *" restart ${SERVICE} "* ]]

# Even if called directly, dry-run must not invoke systemctl.
DRY_RUN=true
SYSTEMCTL_CALLS=()
ensure_service_state
[[ ${#SYSTEMCTL_CALLS[@]} -eq 0 ]]
DRY_RUN=false

# A corrupted installed unit is still an installation failure.
printf '%s\n' '# broken unit' >"${UNIT_DEST}"
if ( fail() { exit 1; }; verify_installation ); then
  echo 'verify_installation accepted a corrupted systemd unit.' >&2
  exit 1
fi

echo 'PASS: B-route runtime prerequisites are optional, installed artifacts remain required.'
