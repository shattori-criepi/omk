#!/usr/bin/env bash

# Install the OMK Chromium kiosk as a user service in an active Wayland session.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=lib/apt-helpers.sh
source "${SCRIPT_DIR}/lib/apt-helpers.sh"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_UID=""
TARGET_GROUP=""
USER_HOME=""
WAYLAND_DISPLAY="${DASHBOARD_KIOSK_WAYLAND_DISPLAY:-}"
DASHBOARD_URL="${DASHBOARD_KIOSK_URL:-http://localhost:8000/}"
DASHBOARD_HEALTH_URL=""
CHROMIUM_PATH="${DASHBOARD_KIOSK_CHROMIUM_PATH:-}"
WTYPE_PATH="${DASHBOARD_KIOSK_WTYPE_PATH:-}"
LABWC_PID="${LABWC_PID:-}"
UNIT_NAME="omk-dashboard-kiosk.service"
UNIT_TEMPLATE="${OMK_ROOT}/systemd/omk-dashboard-kiosk.service.in"
UNIT_DESTINATION=""
KANSHI_DIRECTORY=""
KANSHI_CONFIG=""
KANSHI_DSI_OUTPUT='output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 90'
DRY_RUN=false
PRINT_UNIT=false
PREPARE=false
AS_TARGET=()
USER_SYSTEMD_ENV=()
SUDO=()
UNIT_CHANGED=false

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail() { log "ERROR: $*"; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/setup-dashboard-kiosk.sh [--dry-run|--print-unit|--prepare]

Installs Chromium as the OMK dashboard user systemd service. Run it while the
target user is logged into the Raspberry Pi's Wayland graphical session.

Options:
  --dry-run     Show the resolved user, unit, Chromium, wtype and Wayland settings only.
  --print-unit  Render the resolved unit without writing user configuration.
  --prepare     Install and verify wtype only, before OMK AP activation.
  -h, --help    Show this help.

Environment overrides:
  DASHBOARD_KIOSK_WAYLAND_DISPLAY  Wayland display name (default: wayland-0)
  DASHBOARD_KIOSK_URL              Dashboard base URL (default: http://localhost:8000/)
  DASHBOARD_KIOSK_CHROMIUM_PATH    Chromium executable path (default: detected chromium)
  DASHBOARD_KIOSK_WTYPE_PATH       wtype executable path (default: detected or installed)
  LABWC_PID                        Running labwc PID to use for --reconfigure (optional)
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --print-unit) PRINT_UNIT=true ;;
    --prepare) PREPARE=true ;;
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
KANSHI_DIRECTORY="${USER_HOME}/.config/kanshi"
KANSHI_CONFIG="${KANSHI_DIRECTORY}/config"

if [[ -z "${CHROMIUM_PATH}" ]]; then
  CHROMIUM_PATH="$(command -v chromium || true)"
fi
if [[ -z "${CHROMIUM_PATH}" ]]; then
  CHROMIUM_PATH="not found"
fi
if [[ -z "${WTYPE_PATH}" ]]; then
  WTYPE_PATH="$(command -v wtype || true)"
fi
if [[ -z "${WTYPE_PATH}" ]]; then
  WTYPE_PATH="not found"
fi
DASHBOARD_URL="${DASHBOARD_URL%/}/"
DASHBOARD_HEALTH_URL="${DASHBOARD_URL}health"

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
user_systemctl() { "${AS_TARGET[@]}" "${USER_SYSTEMD_ENV[@]}" systemctl --user "$@"; }

resolve_wayland_display() {
  local manager_display
  if [[ -n "${WAYLAND_DISPLAY}" ]]; then
    return
  fi
  manager_display="$(user_systemctl show-environment | sed -n 's/^WAYLAND_DISPLAY=//p' | head -n 1)"
  WAYLAND_DISPLAY="${manager_display:-wayland-0}"
  if [[ -n "${manager_display}" ]]; then
    log "Using WAYLAND_DISPLAY from user manager: ${WAYLAND_DISPLAY}"
  else
    log "WARN: User manager has no WAYLAND_DISPLAY; using fallback: ${WAYLAND_DISPLAY}"
  fi
}

render_unit() {
  sed \
    -e "s|@OMK_UID@|${TARGET_UID}|g" \
    -e "s|@CHROMIUM_PATH@|${CHROMIUM_PATH}|g" \
    -e "s|@WTYPE_PATH@|${WTYPE_PATH}|g" \
    -e "s|@DASHBOARD_URL@|${DASHBOARD_URL}|g" \
    -e "s|@DASHBOARD_HEALTH_URL@|${DASHBOARD_HEALTH_URL}|g" \
    -e "s|@WAYLAND_DISPLAY@|${WAYLAND_DISPLAY}|g" \
    "${UNIT_TEMPLATE}"
}

install_for_target_user() {
  local source_path="$1"
  local destination_path="$2"
  if ((EUID == 0)); then
    install -o "${TARGET_USER}" -g "${TARGET_GROUP}" -m 0644 "${source_path}" "${destination_path}"
  else
    install -m 0644 "${source_path}" "${destination_path}"
  fi
}

validate_xml() {
  python3 -c 'import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])' "$1"
}

render_kanshi_config() {
  local source_path="$1"
  local destination_path="$2"

  python3 - "${source_path}" "${destination_path}" "${KANSHI_DSI_OUTPUT}" <<'PY'
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
desired = sys.argv[3]
lines = source.read_text(encoding="utf-8").splitlines(keepends=True) if source.exists() else []
dsi_output = re.compile(r"^\s*output\s+DSI-1(?:\s|$)")
rendered = []
replaced = False

for line in lines:
    if dsi_output.match(line):
        if not replaced:
            rendered.append(f"{desired}\n")
            replaced = True
        continue
    rendered.append(line)

if not replaced:
    if rendered and not rendered[-1].endswith("\n"):
        rendered[-1] += "\n"
    rendered.append(f"{desired}\n")

destination.write_text("".join(rendered), encoding="utf-8")
PY
}

update_kanshi_config() {
  local candidate

  "${AS_TARGET[@]}" mkdir -p "${KANSHI_DIRECTORY}"
  candidate="$(mktemp)"
  if ! render_kanshi_config "${KANSHI_CONFIG}" "${candidate}"; then
    rm -f -- "${candidate}"
    fail "Could not render the kanshi configuration: ${KANSHI_CONFIG}"
  fi

  if [[ -f "${KANSHI_CONFIG}" ]] && cmp -s "${candidate}" "${KANSHI_CONFIG}"; then
    rm -f -- "${candidate}"
    log "kanshi DSI-1 rotation is unchanged: ${KANSHI_CONFIG}"
    return
  fi

  install_for_target_user "${candidate}" "${KANSHI_CONFIG}"
  rm -f -- "${candidate}"
  log "Updated kanshi DSI-1 rotation to transform 90: ${KANSHI_CONFIG}"
}

render_labwc_config() {
  local source_path="$1"
  local destination_path="$2"

  python3 - "${source_path}" "${destination_path}" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

OPENBOX_NAMESPACE = "http://openbox.org/3.4/rc"
source, destination = map(Path, sys.argv[1:])


def namespace_declarations(path: Path) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for _, namespace in ET.iterparse(path, events=("start-ns",)):
        prefix, uri = namespace
        declarations[prefix] = uri
    return declarations


def split_tag(tag: object) -> tuple[str | None, str]:
    if not isinstance(tag, str):
        return None, ""
    if tag.startswith("{"):
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return None, tag


def strip_openbox_namespace(element: ET.Element) -> None:
    namespace, local_name = split_tag(element.tag)
    if namespace == OPENBOX_NAMESPACE:
        element.tag = local_name
    for name, value in list(element.attrib.items()):
        namespace, local_name = split_tag(name)
        if namespace == OPENBOX_NAMESPACE:
            del element.attrib[name]
            element.attrib[local_name] = value
    for child in element:
        strip_openbox_namespace(child)


if source.exists():
    for prefix, uri in namespace_declarations(source).items():
        if uri != OPENBOX_NAMESPACE:
            ET.register_namespace(prefix, uri)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(source, parser=parser)
    root = tree.getroot()
    root_namespace, root_name = split_tag(root.tag)
    if root.tag == "openbox_config":
        root.tag = "labwc_config"
    elif root_namespace == OPENBOX_NAMESPACE and root_name == "openbox_config":
        strip_openbox_namespace(root)
        root.tag = "labwc_config"
    elif root.tag != "labwc_config":
        raise ValueError(f"unsupported labwc root element: {root.tag}")
else:
    root = ET.Element("labwc_config")
    tree = ET.ElementTree(root)

keyboard = root.find("keyboard")
if keyboard is None:
    keyboard = ET.Element("keyboard")
    root.insert(0, keyboard)

keybind = next((item for item in keyboard.findall("keybind") if item.get("key") == "A-W-h"), None)
if keybind is None:
    keybind = ET.SubElement(keyboard, "keybind", {"key": "A-W-h"})

if not any(action.get("name") == "HideCursor" for action in keybind.findall("action")):
    ET.SubElement(keybind, "action", {"name": "HideCursor"})
if not any(
    action.get("name") == "WarpCursor"
    and action.get("x") == "-1"
    and action.get("y") == "-1"
    for action in keybind.findall("action")
):
    ET.SubElement(keybind, "action", {"name": "WarpCursor", "x": "-1", "y": "-1"})

tree.write(destination, encoding="utf-8", xml_declaration=True)
ET.parse(destination)
PY
}

update_labwc_config() {
  local labwc_directory="${USER_HOME}/.config/labwc"
  local labwc_config="${labwc_directory}/rc.xml"
  local candidate backup

  "${AS_TARGET[@]}" mkdir -p "${labwc_directory}"
  candidate="$(mktemp)"
  if ! render_labwc_config "${labwc_config}" "${candidate}"
  then
    rm -f -- "${candidate}"
    fail "labwc configuration is not valid XML or has an unsupported root element: ${labwc_config}"
  fi

  if [[ -f "${labwc_config}" ]] && cmp -s "${candidate}" "${labwc_config}"; then
    rm -f -- "${candidate}"
    log "labwc cursor configuration is unchanged: ${labwc_config}"
    return
  fi

  if [[ -e "${labwc_config}" ]]; then
    backup="${labwc_config}.bak.$(date '+%Y%m%d-%H%M%S')"
    "${SUDO[@]}" cp -a "${labwc_config}" "${backup}"
    log "Backed up existing labwc configuration to: ${backup}"
  fi
  install_for_target_user "${candidate}" "${labwc_config}"
  rm -f -- "${candidate}"
  validate_xml "${labwc_config}"
  log "Installed labwc cursor configuration: ${labwc_config}"
}

reconfigure_labwc() {
  local labwc_path=""
  if [[ -n "${LABWC_PID}" ]]; then
    if [[ -r "/proc/${LABWC_PID}/exe" ]]; then
      labwc_path="$(readlink -f "/proc/${LABWC_PID}/exe")"
    else
      log "WARN: LABWC_PID=${LABWC_PID} is not a running process; skipping labwc reload."
      return
    fi
  else
    labwc_path="$(command -v labwc || true)"
  fi

  if [[ -z "${labwc_path}" || ! -x "${labwc_path}" ]]; then
    log "WARN: labwc executable was not found; the cursor setting will apply after the next GUI session. Set LABWC_PID to reload from SSH."
    return
  fi
  if "${AS_TARGET[@]}" "${USER_SYSTEMD_ENV[@]}" "WAYLAND_DISPLAY=${WAYLAND_DISPLAY}" "${labwc_path}" --reconfigure; then
    log "Requested labwc configuration reload."
  else
    log "WARN: labwc reload failed; the cursor setting will apply after the next GUI session."
  fi
}

print_plan() {
  printf '%s\n' \
    "Repository root: ${OMK_ROOT}" \
    "Target user: ${TARGET_USER}" \
    "UID: ${TARGET_UID}" \
    "Chromium path: ${CHROMIUM_PATH}" \
    "wtype path: ${WTYPE_PATH}" \
    "Wayland display: ${WAYLAND_DISPLAY}" \
    "Dashboard URL: ${DASHBOARD_URL}" \
    "Unit destination: ${UNIT_DESTINATION}" \
    "kanshi config: ${KANSHI_CONFIG}" \
    "labwc config: ${USER_HOME}/.config/labwc/rc.xml" \
    "Planned action: daemon-reload, enable, and start/restart ${UNIT_NAME}" \
    "Planned action: set only the DSI-1 kanshi output to transform 90" \
    "Planned action: update labwc HideCursor keybind and request labwc --reconfigure" \
    "Planned check: user systemd bus and /run/user/${TARGET_UID}/${WAYLAND_DISPLAY:-wayland-0}"
}

if "${PRINT_UNIT}"; then
  WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
  print_plan
  printf '\n# %s\n' "${UNIT_NAME}"
  render_unit
  exit 0
fi

if "${DRY_RUN}"; then
  WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
  print_plan
  if [[ "${CHROMIUM_PATH}" == "not found" ]]; then
    log "WARN: Chromium is not installed or not on PATH; a real run would stop."
  fi
  if [[ "${WTYPE_PATH}" == "not found" ]]; then
    log "Would install package: wtype"
  fi
  log "Would update only the DSI-1 output in ${KANSHI_CONFIG} to transform 90; config.init and config.bak are untouched."
  log "Would back up and update ${USER_HOME}/.config/labwc/rc.xml when its cursor keybind changes."
  log "Would validate the labwc XML and request labwc --reconfigure (or apply it at the next GUI session)."
  log "Would read WAYLAND_DISPLAY from the active user manager and confirm its Wayland socket before changing the unit."
  exit 0
fi

if "${PREPARE}"; then
  if [[ "${WTYPE_PATH}" == "not found" || ! -x "${WTYPE_PATH}" ]]; then
    command -v apt-get >/dev/null 2>&1 || fail "wtype is unavailable and apt-get was not found. Install wtype or set DASHBOARD_KIOSK_WTYPE_PATH."
    if ((EUID != 0)); then
      command -v sudo >/dev/null 2>&1 || fail "sudo is required to install wtype when it is missing."
      SUDO=(sudo)
    fi
    log "Installing required package before OMK AP activation: wtype"
    omk_apt "${SUDO[@]}" apt-get update
    omk_apt "${SUDO[@]}" apt-get install -y wtype
    WTYPE_PATH="$(command -v wtype || true)"
  fi
  [[ -n "${WTYPE_PATH}" && -x "${WTYPE_PATH}" ]] || fail "wtype installation did not provide an executable."
  log "SUCCESS: kiosk package preparation is complete: ${WTYPE_PATH}"
  exit 0
fi

[[ "${CHROMIUM_PATH}" != "not found" && -x "${CHROMIUM_PATH}" ]] || fail "Chromium is unavailable. Install chromium or set DASHBOARD_KIOSK_CHROMIUM_PATH."
command -v curl >/dev/null 2>&1 || fail "curl is required to wait for the dashboard health endpoint."
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required."
command -v python3 >/dev/null 2>&1 || fail "python3 is required to update the labwc XML configuration."

if [[ "${WTYPE_PATH}" == "not found" || ! -x "${WTYPE_PATH}" ]]; then
  fail "wtype is unavailable. Run scripts/setup-dashboard-kiosk.sh --prepare before OMK AP activation."
fi
[[ -n "${WTYPE_PATH}" && -x "${WTYPE_PATH}" ]] || fail "wtype installation did not provide an executable."

[[ -S "/run/user/${TARGET_UID}/bus" ]] || fail "No user systemd bus for ${TARGET_USER}. Log into the graphical session first."
[[ -d "/run/user/${TARGET_UID}" ]] || fail "Runtime directory is unavailable for ${TARGET_USER}. Log into the graphical session first."
resolve_wayland_display
[[ -S "/run/user/${TARGET_UID}/${WAYLAND_DISPLAY}" ]] || fail "Wayland socket is unavailable. Confirm a Wayland GUI session and DASHBOARD_KIOSK_WAYLAND_DISPLAY."

CONFIG_DIRECTORY="${USER_HOME}/.config/systemd/user"
"${AS_TARGET[@]}" mkdir -p "${CONFIG_DIRECTORY}"
update_kanshi_config
update_labwc_config
reconfigure_labwc

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
  install_for_target_user "${TEMP_UNIT}" "${UNIT_DESTINATION}"
  UNIT_CHANGED=true
  log "Installed user unit: ${UNIT_DESTINATION}"
fi

OLD_WANTS_DIRECTORY="${USER_HOME}/.config/systemd/user/graphical-session.target.wants"
OLD_WANTS_LINK="${OLD_WANTS_DIRECTORY}/${UNIT_NAME}"
if [[ -L "${OLD_WANTS_LINK}" ]]; then
  "${AS_TARGET[@]}" rm -f -- "${OLD_WANTS_LINK}"
  log "Removed obsolete graphical-session.target symlink: ${OLD_WANTS_LINK}"
elif [[ -e "${OLD_WANTS_LINK}" ]]; then
  log "WARN: Obsolete target entry is not a symlink; leaving it unchanged: ${OLD_WANTS_LINK}"
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
grep -q '<labwc_config' "${USER_HOME}/.config/labwc/rc.xml" || fail "labwc configuration does not have a labwc_config root."
grep -q 'name="HideCursor"' "${USER_HOME}/.config/labwc/rc.xml" || fail "labwc HideCursor action is missing."
grep -q 'name="WarpCursor"' "${USER_HOME}/.config/labwc/rc.xml" || fail "labwc WarpCursor action is missing."
grep -Fq 'ExecStartPost=/bin/sh -c' "${UNIT_DESTINATION}" || fail "Kiosk unit does not contain the wtype ExecStartPost command."
log "wtype is available: ${WTYPE_PATH}"
WTYPE_POST_RESULT="$(user_systemctl show "${UNIT_NAME}" --property=ExecStartPost --value)"
if [[ "${WTYPE_POST_RESULT}" == *"status=0"* ]]; then
  log "wtype cursor-hide command completed successfully."
else
  log "WARN: Could not confirm the wtype cursor-hide exit status; inspect the user journal if the cursor remains visible."
fi

MAIN_PID="$(user_systemctl show -p MainPID --value "${UNIT_NAME}")"
if [[ "${MAIN_PID}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${MAIN_PID}" 2>/dev/null; then
  log "Chromium kiosk process is running: PID=${MAIN_PID}"
else
  fail "Kiosk service is active but its MainPID is unavailable; inspect the user journal."
fi

log "Setup completed. Check with:"
log "  systemctl --user status ${UNIT_NAME} --no-pager"
log "  systemctl --user restart ${UNIT_NAME}"
log "  journalctl --user -u ${UNIT_NAME} --no-pager"
log "Stop with: systemctl --user disable --now ${UNIT_NAME}"
