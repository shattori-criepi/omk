#!/usr/bin/env bash

# Exercise finite apt/dpkg lock waiting without touching the host package DB.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
source "${ROOT}/scripts/lib/apt-helpers.sh"

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
MOCK_APT="${TEMP_DIR}/apt-get"
COUNT_FILE="${TEMP_DIR}/count"
SLEEP_FILE="${TEMP_DIR}/sleeps"

cat >"${MOCK_APT}" <<'EOF'
#!/usr/bin/env bash
count=0
[[ -f "${MOCK_APT_COUNT_FILE}" ]] && count="$(<"${MOCK_APT_COUNT_FILE}")"
count=$((count + 1))
printf '%s' "${count}" >"${MOCK_APT_COUNT_FILE}"
case "${MOCK_APT_MODE}" in
  unlock-after)
    if ((count <= MOCK_APT_LOCK_ATTEMPTS)); then
      printf '%s\n' 'E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 123 (apt-get)' >&2
      exit 100
    fi
    printf '%s\n' 'Reading package lists... Done'
    ;;
  locked)
    printf '%s\n' 'E: Unable to acquire the dpkg frontend lock' >&2
    exit 100
    ;;
  broken)
    printf '%s\n' 'E: The repository does not have a Release file.' >&2
    exit 100
    ;;
  success) printf '%s\n' 'Reading package lists... Done' ;;
esac
EOF
chmod +x "${MOCK_APT}"

# Do not make the regression wait for real time, while recording every retry.
sleep() { printf '%s\n' "$1" >>"${SLEEP_FILE}"; }
# Holder discovery is covered by its small, host-safe implementation; suppress
# host fuser/ps output here so lock timing expectations stay deterministic.
omk_apt_lock_holders() { :; }
export MOCK_APT_COUNT_FILE="${COUNT_FILE}"

rm -f "${COUNT_FILE}" "${SLEEP_FILE}"
MOCK_APT_MODE=unlock-after MOCK_APT_LOCK_ATTEMPTS=2 \
  OMK_APT_LOCK_TIMEOUT_SECONDS=30 OMK_APT_LOCK_RETRY_SECONDS=1 omk_apt "${MOCK_APT}" update >/dev/null
[[ "$(<"${COUNT_FILE}")" == 3 ]]
[[ "$(wc -l <"${SLEEP_FILE}")" == 2 ]]

rm -f "${COUNT_FILE}" "${SLEEP_FILE}"
if MOCK_APT_MODE=locked OMK_APT_LOCK_TIMEOUT_SECONDS=0 omk_apt "${MOCK_APT}" update >"${TEMP_DIR}/timeout.log" 2>&1; then
  echo 'Persistent apt lock unexpectedly succeeded.' >&2
  exit 1
fi
grep -Fq 'lock remained unavailable' "${TEMP_DIR}/timeout.log"
[[ "$(<"${COUNT_FILE}")" == 1 ]]

rm -f "${COUNT_FILE}" "${SLEEP_FILE}"
if MOCK_APT_MODE=broken OMK_APT_LOCK_TIMEOUT_SECONDS=30 omk_apt "${MOCK_APT}" update >"${TEMP_DIR}/broken.log" 2>&1; then
  echo 'Non-lock apt error unexpectedly succeeded.' >&2
  exit 1
fi
grep -Fq 'this is not a lock conflict' "${TEMP_DIR}/broken.log"
[[ "$(<"${COUNT_FILE}")" == 1 ]]
[[ ! -e "${SLEEP_FILE}" ]]

rm -f "${COUNT_FILE}" "${SLEEP_FILE}"
MOCK_APT_MODE=success OMK_APT_LOCK_TIMEOUT_SECONDS=30 omk_apt "${MOCK_APT}" update >/dev/null
[[ "$(<"${COUNT_FILE}")" == 1 ]]
[[ ! -e "${SLEEP_FILE}" ]]

echo 'PASS: apt lock waits are bounded, retry only lock conflicts, and preserve normal failures.'
