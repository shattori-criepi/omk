#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SCRIPT="${ROOT_DIR}/scripts/setup-soracom-onyx.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/bin" "${TEMP_DIR}/dispatcher" "${TEMP_DIR}/etc/omk"

source <(sed -n '/^dispatcher_metadata_is_safe()/,/^repair_dispatcher_routes_without_reconnect()/p' "${SCRIPT}" | sed '$d')

cat >"${TEMP_DIR}/bin/stat" <<'EOF'
#!/usr/bin/env bash
path="${@: -1}"
if [[ "${path}" == *legacy-* ]]; then printf '0:0:600\n'; else printf '0:0:755\n'; fi
EOF
cat >"${TEMP_DIR}/bin/sha256sum" <<'EOF'
#!/usr/bin/env bash
path="$1"
if [[ "${path}" == *omk-soracom-legacy.* && "${OMK_TEST_PRIVILEGED_READ:-no}" != yes ]]; then
  exit 13
fi
if [[ "${FAIL_LEGACY_BACKUP_HASH_ONCE:-no}" == yes && "${path}" == *omk-soracom-legacy.* && ! -e "${FIXTURE}/backup-hash-failed" ]]; then
  touch "${FIXTURE}/backup-hash-failed"
  exit 14
fi
if grep -Fxq LEGACY_KNOWN "${path}"; then
  printf '%s  %s\n' fcb91353b7e55d644a0b22e032c0409521d65b18f4e4205306e562db080ba7d0 "${path}"
else
  /usr/bin/sha256sum "${path}"
fi
EOF
cat >"${TEMP_DIR}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
OMK_TEST_PRIVILEGED_READ=yes exec "$@"
EOF
cat >"${TEMP_DIR}/bin/install" <<'EOF'
#!/usr/bin/env bash
if [[ " $* " == *' -d '* ]]; then mkdir -p "${@: -1}"; exit 0; fi
mode=''
previous=''
for argument in "$@"; do
  if [[ "${previous}" == -m ]]; then mode="${argument}"; fi
  previous="${argument}"
done
source="${@: -2:1}"; destination="${@: -1}"
cp "${source}" "${destination}"
[[ -z "${mode}" ]] || chmod "${mode}" "${destination}"
EOF
cat >"${TEMP_DIR}/bin/mktemp" <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/mktemp "$@"
EOF
cat >"${TEMP_DIR}/bin/mv" <<'EOF'
#!/usr/bin/env bash
destination="${@: -1}"
if [[ "${FAIL_REPLACEMENT_ONCE:-no}" == yes && "${destination}" == *dispatcher/90.soracom_route && ! -e "${FIXTURE}/replacement-failed" ]]; then
  touch "${FIXTURE}/replacement-failed"
  exit 15
fi
exec /usr/bin/mv "$@"
EOF
cat >"${TEMP_DIR}/bin/rm" <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/rm "$@"
EOF
chmod +x "${TEMP_DIR}/bin/"*

export PATH="${TEMP_DIR}/bin:${PATH}"
export FIXTURE="${TEMP_DIR}"
SCRIPT_DIR="${ROOT_DIR}/scripts"
SUDO=(sudo)
APN=soracom.io
LEGACY_SORACOM_DISPATCHER_SHA256="fcb91353b7e55d644a0b22e032c0409521d65b18f4e4205306e562db080ba7d0"
SORACOM_DISPATCHER_PATH="${TEMP_DIR}/dispatcher/90.soracom_route"
SORACOM_LEGACY_BACKUP_PATH="${TEMP_DIR}/etc/omk/legacy-soracom-route.sh"
log() { :; }

# Exact legacy hash is the only migration input. It is backed up root-only,
# then atomically replaced by the current dispatcher; a rerun is a no-op.
printf 'LEGACY_KNOWN\n' >"${SORACOM_DISPATCHER_PATH}"
chmod 0755 "${SORACOM_DISPATCHER_PATH}"
ensure_current_soracom_dispatcher
cmp -s "${SCRIPT_DIR}/soracom-route-dispatcher" "${SORACOM_DISPATCHER_PATH}"
! file_has_legacy_hash_as_user "${SORACOM_LEGACY_BACKUP_PATH}"
file_has_legacy_hash_as_root "${SORACOM_LEGACY_BACKUP_PATH}"
sudo grep -Fxq LEGACY_KNOWN "${SORACOM_LEGACY_BACKUP_PATH}"
before="$(sudo cksum <"${SORACOM_LEGACY_BACKUP_PATH}")"
ensure_current_soracom_dispatcher
[[ "$(sudo cksum <"${SORACOM_LEGACY_BACKUP_PATH}")" == "${before}" ]]

# A failure while validating a root-only temporary backup removes that staging
# file, leaves the legacy dispatcher untouched, and a rerun can migrate it.
rm -f -- "${SORACOM_LEGACY_BACKUP_PATH}"
printf 'LEGACY_KNOWN\n' >"${SORACOM_DISPATCHER_PATH}"
chmod 0755 "${SORACOM_DISPATCHER_PATH}"
export FAIL_LEGACY_BACKUP_HASH_ONCE=yes
! ensure_current_soracom_dispatcher
unset FAIL_LEGACY_BACKUP_HASH_ONCE
[[ ! -e "${SORACOM_LEGACY_BACKUP_PATH}" ]]
grep -Fxq LEGACY_KNOWN "${SORACOM_DISPATCHER_PATH}"
ensure_current_soracom_dispatcher
file_has_legacy_hash_as_root "${SORACOM_LEGACY_BACKUP_PATH}"

# If replacement fails after publishing the verified backup, rerun validates
# that root-only backup and completes migration without recreating it.
rm -f -- "${SORACOM_LEGACY_BACKUP_PATH}"
printf 'LEGACY_KNOWN\n' >"${SORACOM_DISPATCHER_PATH}"
chmod 0755 "${SORACOM_DISPATCHER_PATH}"
export FAIL_REPLACEMENT_ONCE=yes
! ensure_current_soracom_dispatcher
unset FAIL_REPLACEMENT_ONCE
[[ -e "${SORACOM_LEGACY_BACKUP_PATH}" ]]
file_has_legacy_hash_as_root "${SORACOM_LEGACY_BACKUP_PATH}"
grep -Fxq LEGACY_KNOWN "${SORACOM_DISPATCHER_PATH}"
ensure_current_soracom_dispatcher
cmp -s "${SCRIPT_DIR}/soracom-route-dispatcher" "${SORACOM_DISPATCHER_PATH}"

# A current dispatcher is accepted without creating a migration backup.
rm -f -- "${SORACOM_LEGACY_BACKUP_PATH}"
cp "${SCRIPT_DIR}/soracom-route-dispatcher" "${SORACOM_DISPATCHER_PATH}"
ensure_current_soracom_dispatcher
[[ ! -e "${SORACOM_LEGACY_BACKUP_PATH}" ]]

# Unknown content, even with safe ownership/mode, is neither executed nor
# overwritten and never copied into the backup location.
printf 'UNKNOWN_USER_DISPATCHER\n' >"${SORACOM_DISPATCHER_PATH}"
chmod 0755 "${SORACOM_DISPATCHER_PATH}"
! ensure_current_soracom_dispatcher
grep -Fxq UNKNOWN_USER_DISPATCHER "${SORACOM_DISPATCHER_PATH}"
[[ ! -e "${SORACOM_LEGACY_BACKUP_PATH}" ]]

echo 'PASS: only the exact legacy SORACOM dispatcher is migrated; current and unknown files are preserved safely.'
