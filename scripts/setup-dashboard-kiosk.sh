#!/usr/bin/env bash

# Install the OMK Chromium kiosk as a user service in an active Wayland session.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_UID=""
TARGET_GROUP=""
USER_HOME=""
WAYLAND_DISPLAY="${DASHBOARD_KIOSK_WAYLAND_DISPLAY:-wayland-0}"
DASHBOARD_URL="${DASHBOARD_KIOSK_URL:-http://localhost:8000/}"
CHROMIUM_PATH="${DASHBOARD_KIOSK_CHROMIUM_PATH:-}"
UNIT_NAME="omk-dashboard-kiosk.service"
UNIT_TEMPLATE="${OMK_ROOT}/systemd/omk-dashboard-kiosk.service.in"
UNIT_DESTINATION=""
DRY_RUN=false
PRINT_UNIT=false
AS_TARGET=()
USER_SYSTEMD_ENV=()
UNIT_CHANGED=false

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail() { log "ERROR: $*"; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/setup-dashboard-kiosk.sh [--dry-run|--print-unit]

Installs Chromium as the OMK dashboard user systemd service. Run it while the
target user is logged into the Raspberry Pi's Wayland graphical session.

Options:
  --dry-run     Show the resolved user, unit, Chromium and Wayland settings only.
  --print-unit  Render the resolved unit without writing user configuration.
  -h, --help    Show this help.

Environment overrides:
  DASHBOARD_KIOSK_WAYLAND_DISPLAY  Wayland display name (default: wayland-0)
  DASHBOARD_KIOSK_URL              Dashboard base URL (default: http://localhost:8000/)
  DASHBOARD_KIOSK_CHROMIUM_PATH    Chromium executable path (default: detected chromium)
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --print-unit) PRINT_UNIT=true ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$(uname -s)" == Linux ]] || fail "Linux is required."
id "${TARGET_USER}" >/dev/null 2>&1 || fail "Target user does not exist: ${TARGET_USER}"
[[ -f "${UNIT_TEMPLATE}" ]] || fail "Unit template is missing: ${UNIT_TEMPLATE}"

TARGET_UID="$(id -u "${TARGET_USER}")"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
USER_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
[[ -n "${USER_HOME}" && -d "${USER_HOME}" ]] || fail "Home directory is unavailable for ${TARGET_USER}."
UNIT_DESTINATION="${USER_HOME}/.config/systemd/user/${UNIT_NAME}"

if [[ -z "${CHROMIUM_PATH}" ]]; then
  CHROMIUM_PATH="$(command -v chromium || true)"
fi
if [[ -z "${CHROMIUM_PATH}" ]]; then
  CHROMIUM_PATH="not found"
fi
DASHBOARD_URL="${DASHBOARD_URL%/}/"

if ((EUID == 0)) && [[ "${TARGET_USER}" != root ]]; then
  if command -v runuser >/dev/null 2>&1; then
    AS_TARGET=(runuser -u "${TARGET_USER}" --)
  elif command -v sudo >/dev/null 2>&1; then
    AS_TARGET=(sudo -u "${TARGET_USER}")
  else
    fail "runuser or sudo is required to operate the user service as ${TARGET_USER}."
  fi
elif ((EUID != 0)) && [[ "${TARGET_USER}" != "$(id -un)" ]]; then
  fail "Run as ${TARGET_USER} or use sudo so the target user can be determined."
fi

USER_SYSTEMD_ENV=(env "XDG_RUNTIME_DIR=/run/user/${TARGET_UID}" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${TARGET_UID}/bus")

render_unit() {
  sed \
    -e "s|@OMK_UID@|${TARGET_UID}|g" \
    -e "s|@CHROMIUM_PATH@|${CHROMIUM_PATH}|g" \
    -e "s|@DASHBOARD_URL@|${DASHBOARD_URL}|g" \
    -e "s|@WAYLAND_DISPLAY@|${WAYLAND_DISPLAY}|g" \
    "${UNIT_TEMPLATE}"
}

print_plan() {
  printf '%s\n' \
    "Repository root: ${OMK_ROOT}" \
    "Target user: ${TARGET_USER}" \
    "UID: ${TARGET_UID}" \
    "Chromium path: ${CHROMIUM_PATH}" \
    "Wayland display: ${WAYLAND_DISPLAY}" \
    "Dashboard URL: ${DASHBOARD_URL}" \
    "Unit destination: ${UNIT_DESTINATION}" \
    "Planned action: daemon-reload, enable, and start/restart ${UNIT_NAME}" \
    "Planned check: graphical-session.target and /run/user/${TARGET_UID}/${WAYLAND_DISPLAY}"
}

if "${PRINT_UNIT}"; then
  print_plan
  printf '\n# %s\n' "${UNIT_NAME}"
  render_unit
  exit 0
fi

if "${DRY_RUN}"; then
  print_plan
  if [[ "${CHROMIUM_PATH}" == "not found" ]]; then
    log "WARN: Chromium is not installed or not on PATH; a real run would stop."
  fi
  log "Would confirm curl, the active user manager, graphical-session.target, and Wayland socket before changing the unit."
  exit 0
fi

[[ "${CHROMIUM_PATH}" != "not found" && -x "${CHROMIUM_PATH}" ]] || fail "Chromium is unavailable. Install chromium or set DASHBOARD_KIOSK_CHROMIUM_PATH."
command -v curl >/dev/null 2>&1 || fail "curl is required to wait for the dashboard health endpoint."
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required."
command -v pgrep >/dev/null 2>&1 || fail "pgrep is required to verify Chromium."

user_systemctl() { "${AS_TARGET[@]}" "${USER_SYSTEMD_ENV[@]}" systemctl --user "$@"; }

[[ -S "/run/user/${TARGET_UID}/bus" ]] || fail "No user systemd bus for ${TARGET_USER}. Log into the graphical session first."
[[ -S "/run/user/${TARGET_UID}/${WAYLAND_DISPLAY}" ]] || fail "Wayland socket is unavailable. Confirm a Wayland GUI session and DASHBOARD_KIOSK_WAYLAND_DISPLAY."
user_systemctl is-active --quiet graphical-session.target || fail "graphical-session.target is inactive. Run this from or after the target user's GUI login."

CONFIG_DIRECTORY="${USER_HOME}/.config/systemd/user"
"${AS_TARGET[@]}" mkdir -p "${CONFIG_DIRECTORY}"

TEMP_UNIT="$(mktemp)"
trap 'rm -f -- "${TEMP_UNIT}"' EXIT
render_unit >"${TEMP_UNIT}"
if [[ -f "${UNIT_DESTINATION}" ]] && cmp -s "${TEMP_UNIT}" "${UNIT_DESTINATION}"; then
  log "Unit is unchanged; preserving it: ${UNIT_DESTINATION}"
else
  if [[ -e "${UNIT_DESTINATION}" ]]; then
    BACKUP="${UNIT_DESTINATION}.bak.$(date '+%Y%m%d-%H%M%S')"
    "${AS_TARGET[@]}" cp -a "${UNIT_DESTINATION}" "${BACKUP}"
    log "Backed up existing user unit to: ${BACKUP}"
  fi
  "${AS_TARGET[@]}" install -m 0644 "${TEMP_UNIT}" "${UNIT_DESTINATION}"
  UNIT_CHANGED=true
  log "Installed user unit: ${UNIT_DESTINATION}"
fi

curl --fail --silent --show-error "${DASHBOARD_URL}health" >/dev/null || fail "Dashboard health endpoint is unavailable: ${DASHBOARD_URL}health"
user_systemctl daemon-reload
user_systemctl enable "${UNIT_NAME}"
if "${UNIT_CHANGED}" && user_systemctl is-active --quiet "${UNIT_NAME}"; then
  user_systemctl restart "${UNIT_NAME}"
else
  user_systemctl start "${UNIT_NAME}"
fi
user_systemctl is-active --quiet "${UNIT_NAME}" || fail "Kiosk service did not become active."

for _attempt in 1 2 3 4 5; do
  if "${AS_TARGET[@]}" pgrep -u "${TARGET_USER}" -f "${CHROMIUM_PATH}.*${DASHBOARD_URL}display" >/dev/null; then
    log "Chromium kiosk process is running."
    log "Setup completed. Check with:"
    log "  systemctl --user status ${UNIT_NAME} --no-pager"
    log "  systemctl --user restart ${UNIT_NAME}"
    log "  journalctl --user -u ${UNIT_NAME} --no-pager"
    log "Stop with: systemctl --user disable --now ${UNIT_NAME}"
    exit 0
  fi
  sleep 1
done
fail "Kiosk service is active but Chromium was not detected; inspect the user journal."
