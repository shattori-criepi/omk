#!/usr/bin/env bash
# Install the reusable OMK CSV export CLI without touching measurement data.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=lib/apt-helpers.sh
source "${SCRIPT_DIR}/lib/apt-helpers.sh"
# shellcheck source=lib/python-src-import-smoke.sh
source "${SCRIPT_DIR}/lib/python-src-import-smoke.sh"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_UID="$(id -u "${TARGET_USER}")"
TARGET_GID="$(id -g "${TARGET_USER}")"
SUDO=()
(( EUID == 0 )) || SUDO=(sudo)
AS_TARGET=()
(( EUID == 0 )) && AS_TARGET=(runuser -u "${TARGET_USER}" --) || AS_TARGET=(sudo -u "${TARGET_USER}")
CLI_BIN_DIR="${OMK_EXPORT_BIN_DIR:-/usr/local/bin}"
HELPER_SOURCE="${OMK_ROOT}/scripts/omk-export-usb-helper"
HELPER_DEST="/usr/local/libexec/omk-export-usb-helper"
SUDOERS_DEST="/etc/sudoers.d/omk-export-usb"

install_cli_launcher() {
  local name="$1" temporary_launcher

  temporary_launcher="$(mktemp)"
  printf '#!/usr/bin/env bash\nexec %q "$@"\n' "${OMK_ROOT}/scripts/${name}" >"${temporary_launcher}"
  if ! "${SUDO[@]}" install -D -m 0755 "${temporary_launcher}" "${CLI_BIN_DIR}/${name}"; then
    rm -f -- "${temporary_launcher}"
    return 1
  fi
  rm -f -- "${temporary_launcher}"
}

render_sudoers() {
  printf '%s ALL=(root) NOPASSWD: %s ^(mount|unmount) [0-9a-f]{64}$\n' "${TARGET_USER}" "${HELPER_DEST}"
}

[[ -f "${OMK_ROOT}/services/data-exporter/requirements.txt" ]] || { echo 'ERROR: OMK repository root not found.' >&2; exit 1; }
[[ -f "${HELPER_SOURCE}" ]] || { echo 'ERROR: USB helper source is unavailable.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'ERROR: python3 is required.' >&2; exit 1; }
command -v visudo >/dev/null || { echo 'ERROR: visudo is required.' >&2; exit 1; }
NSENTER="$(command -v nsenter || true)"
[[ -x "${NSENTER}" ]] || { echo 'ERROR: nsenter is required for safe USB mount operations.' >&2; exit 1; }
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo 'ERROR: python3-venv is required.' >&2
  exit 1
fi
VENV="${OMK_ROOT}/services/data-exporter/.venv"
[[ -d "${VENV}" ]] || "${AS_TARGET[@]}" python3 -m venv "${VENV}"
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install --upgrade pip
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install -r "${OMK_ROOT}/services/data-exporter/requirements.txt"
omk_python_src_import_smoke "${OMK_ROOT}/services/data-exporter/src" data_exporter "${VENV}/bin/python" "${AS_TARGET[@]}" ||
  { echo 'ERROR: data-exporter import smoke test failed.' >&2; exit 1; }
temporary_helper="$(mktemp)"
temporary_sudoers="$(mktemp)"
trap 'rm -f -- "${temporary_helper}" "${temporary_sudoers}"' EXIT
sed -e "s|@TARGET_UID@|${TARGET_UID}|g" -e "s|@TARGET_GID@|${TARGET_GID}|g" -e "s|@NSENTER@|${NSENTER}|g" "${HELPER_SOURCE}" >"${temporary_helper}"
render_sudoers >"${temporary_sudoers}"
"${SUDO[@]}" visudo -cf "${temporary_sudoers}"
"${SUDO[@]}" install -D -o root -g root -m 0755 "${temporary_helper}" "${HELPER_DEST}"
"${SUDO[@]}" install -D -o root -g root -m 0440 "${temporary_sudoers}" "${SUDOERS_DEST}"
install_cli_launcher omk-export
install_cli_launcher omk-export-usb
echo 'Installed omk-export and omk-export-usb.'
