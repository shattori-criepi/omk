#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATEWAY_HARNESS_SOURCE_ROOT="${ROOT}"
source "${ROOT}/scripts/tests/lib/gateway_harness.sh"

fresh_fixture() {
  unset OMK_SKIP_PREBASE_PREFLIGHT OMK_PREBASE_MIN_FREE_KIB
  unset GW_FAIL_COMMAND GW_FAIL_SUBCOMMAND GW_FAIL_MODE GW_FAIL_COUNT GW_FAIL_STATUS
  gateway_harness_create
}

expect_prebase_failure() {
  local case_name="$1" expected="$2" status
  if gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/${case_name}.log" 2>&1; then
    echo "FAIL: ${case_name} unexpectedly succeeded." >&2
    exit 1
  else
    status=$?
  fi
  [[ "${status}" == 1 ]]
  grep -Fq "${expected}" "${GATEWAY_HARNESS_ROOT}/${case_name}.log"
  if grep -Fq 'apt-get update' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
  if grep -Fq 'apt-get full-upgrade' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
  if grep -Fq 'apt-get install' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
  if grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
}

write_low_space_df() {
  local available_kib="$1"
  rm -f "${GATEWAY_HARNESS_ROOT}/bin/df"
  cat >"${GATEWAY_HARNESS_ROOT}/bin/df" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "df \$*" >>"\${GW_CALL_LOG}"
printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on' '/dev/fake 20000000 1000 ${available_kib} 1% /'
EOF
  chmod +x "${GATEWAY_HARNESS_ROOT}/bin/df"
}

# T1: normal fixture remains the sole success baseline.
fresh_fixture
gateway_harness_run --with-base >/dev/null
grep -Fq 'apt-get update' "${GW_CALL_LOG}"
gateway_harness_destroy

# F1: only the Pi model differs.
fresh_fixture
printf 'Raspberry Pi 5 Model B' >"${OMK_TEST_DEVICE_MODEL}"
expect_prebase_failure F1 'Raspberry Pi 4 is required'
gateway_harness_destroy

# F2: only dpkg architecture differs.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/dpkg"
cat >"${GATEWAY_HARNESS_ROOT}/bin/dpkg" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' dpkg "$*" >>"${GW_CALL_LOG}"
[[ "$*" == *--print-architecture* ]] && printf '%s\n' armhf
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/dpkg"
expect_prebase_failure F2 'arm64 userland is required (got armhf)'
gateway_harness_destroy

# F3: only os-release differs; production currently contracts on a supported ID.
fresh_fixture
printf 'ID=ubuntu\nVERSION_ID=24.04\n' >"${OMK_TEST_OS_RELEASE}"
expect_prebase_failure F3 'Raspberry Pi OS/Debian is required (got ubuntu 24.04)'
gateway_harness_destroy

# F4: free space is one KiB below the production threshold, then exactly at it.
fresh_fixture
write_low_space_df 8388607
expect_prebase_failure F4 'free disk is 8388607 KiB; at least 8388608 KiB is required'
gateway_harness_destroy

fresh_fixture
write_low_space_df 8388608
gateway_harness_run --with-base >/dev/null
grep -Fq 'apt-get update' "${GW_CALL_LOG}"
gateway_harness_destroy

# F5: only sudo authentication fails; no root fallback is permitted.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/sudo"
cat >"${GATEWAY_HARNESS_ROOT}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' sudo "$*" >>"${GW_CALL_LOG}"
exit 1
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/sudo"
expect_prebase_failure F5 'sudo authorization failed'
gateway_harness_destroy

# F6: only the wall clock predates the allowed HTTPS range.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/date"
cat >"${GATEWAY_HARNESS_ROOT}/bin/date" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' date "$*" >>"${GW_CALL_LOG}"
printf '%s\n' 1704067199
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/date"
expect_prebase_failure F6 'system clock predates 2024-01-01'
gateway_harness_destroy

# The exact 2024-01-01 epoch remains a valid normal clock boundary.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/date"
cat >"${GATEWAY_HARNESS_ROOT}/bin/date" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' date "$*" >>"${GW_CALL_LOG}"
printf '%s\n' 1704067200
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/date"
gateway_harness_run --with-base >/dev/null
grep -Fq 'apt-get update' "${GW_CALL_LOG}"
gateway_harness_destroy

# F7: only the default route is absent; DNS must not be attempted.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/ip"
cat >"${GATEWAY_HARNESS_ROOT}/bin/ip" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' ip "$*" >>"${GW_CALL_LOG}"
exit 0
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/ip"
expect_prebase_failure F7 'no default network route is available'
if grep -Fq 'getent ahosts' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
gateway_harness_destroy

# F8: route remains normal; only the first repository DNS lookup fails.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/getent"
cat >"${GATEWAY_HARNESS_ROOT}/bin/getent" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' getent "$*" >>"${GW_CALL_LOG}"
[[ "$*" == *deb.debian.org* ]] && exit 1
printf '%s\n' '192.0.2.2 STREAM archive.raspberrypi.com'
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/getent"
expect_prebase_failure F8 'DNS cannot resolve deb.debian.org'
grep -Fq 'ip route show default' "${GW_CALL_LOG}"
gateway_harness_destroy

# F9: only wlan0 is absent from the network fixture.
fresh_fixture
rmdir "${OMK_TEST_SYS_CLASS_NET}/wlan0"
expect_prebase_failure F9 'wlan0 is unavailable'
gateway_harness_destroy

# F10: wlan0 remains present but iw positively reports no AP capability.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/iw"
cat >"${GATEWAY_HARNESS_ROOT}/bin/iw" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' iw "$*" >>"${GW_CALL_LOG}"
printf '%s\n' 'Supported interface modes:' ' * managed'
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/iw"
expect_prebase_failure F10 'does not report AP-mode support'
gateway_harness_destroy

# If iw is unavailable rather than negative, production intentionally warns and continues.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/iw"
gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/iw-unavailable.log" 2>&1
grep -Fq 'iw is not installed yet' "${GATEWAY_HARNESS_ROOT}/iw-unavailable.log"
grep -Fq 'apt-get update' "${GW_CALL_LOG}"
gateway_harness_destroy

# F11: only dpkg audit emits unfinished package state.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/dpkg"
cat >"${GATEWAY_HARNESS_ROOT}/bin/dpkg" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' dpkg "$*" >>"${GW_CALL_LOG}"
case "$*" in
  *--print-architecture*) printf '%s\n' arm64 ;;
  *--audit*) printf '%s\n' 'packages need configuration' ;;
esac
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/dpkg"
expect_prebase_failure F11 'dpkg reports unfinished package configuration'
gateway_harness_destroy

# F12: audit remains clean; only apt-get check is inconsistent.
fresh_fixture
rm -f "${GATEWAY_HARNESS_ROOT}/bin/apt-get"
cat >"${GATEWAY_HARNESS_ROOT}/bin/apt-get" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' apt-get "$*" >>"${GW_CALL_LOG}"
[[ "$*" == *check* ]] && exit 42
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/apt-get"
expect_prebase_failure F12 'apt package state is inconsistent'
grep -Fq 'apt-get check -o DPkg::Lock::Timeout=30 -o Acquire::Retries=3' "${GW_CALL_LOG}"
gateway_harness_destroy

echo 'PASS: all F1-F12 pre-base failures stop before base apt operations and AP activation.'
