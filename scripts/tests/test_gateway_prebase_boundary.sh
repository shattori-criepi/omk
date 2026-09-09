#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATEWAY_HARNESS_SOURCE_ROOT="${ROOT}"
source "${ROOT}/scripts/tests/lib/gateway_harness.sh"

# T1: normal fixture runs the real pre-base function without its skip override.
gateway_harness_create
unset OMK_SKIP_PREBASE_PREFLIGHT
gateway_harness_run --with-base >/dev/null
grep -Fq 'apt-get update' "${GW_CALL_LOG}"
gateway_harness_destroy

# T2: only the model fixture changes; no base apt/AP operation is reached.
gateway_harness_create
printf 'Raspberry Pi 5 Model B' >"${OMK_TEST_DEVICE_MODEL}"
if gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/t2.log" 2>&1; then exit 1; fi
grep -Fq 'Raspberry Pi 4 is required' "${GATEWAY_HARNESS_ROOT}/t2.log"
if grep -Fq 'apt-get update' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
if grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
gateway_harness_destroy

# T3: df is a PATH stub, so a low-space fixture fails production preflight.
gateway_harness_create
rm -f "${GATEWAY_HARNESS_ROOT}/bin/df"
cat >"${GATEWAY_HARNESS_ROOT}/bin/df" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on' '/dev/fake 1000 900 100 90% /'
EOF
chmod +x "${GATEWAY_HARNESS_ROOT}/bin/df"
if gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/t3.log" 2>&1; then exit 1; fi
grep -Fq 'free disk is 100 KiB' "${GATEWAY_HARNESS_ROOT}/t3.log"
if grep -Fq 'apt-get update' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
if grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
gateway_harness_destroy
echo 'PASS: pre-base preflight uses fixture paths and PATH commands without a skip override.'
