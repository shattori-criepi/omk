#!/usr/bin/env bash
# Install the reusable OMK CSV export CLI without touching measurement data.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
SUDO=()
(( EUID == 0 )) || SUDO=(sudo)
AS_TARGET=()
(( EUID == 0 )) && AS_TARGET=(runuser -u "${TARGET_USER}" --) || AS_TARGET=(sudo -u "${TARGET_USER}")

[[ -f "${OMK_ROOT}/services/data-exporter/requirements.txt" ]] || { echo 'ERROR: OMK repository root not found.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'ERROR: python3 is required.' >&2; exit 1; }
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo 'ERROR: python3-venv is required.' >&2
  exit 1
fi
VENV="${OMK_ROOT}/services/data-exporter/.venv"
[[ -d "${VENV}" ]] || "${AS_TARGET[@]}" python3 -m venv "${VENV}"
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install --upgrade pip
"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip install -r "${OMK_ROOT}/services/data-exporter/requirements.txt"
"${SUDO[@]}" install -m 0755 "${OMK_ROOT}/scripts/omk-export" /usr/local/bin/omk-export
echo 'Installed omk-export. Example: omk-export --from 2026-08-01 --to 2026-08-31'
