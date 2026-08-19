#!/usr/bin/env bash

# Check that the thin Gateway entry point keeps setup work in individual scripts.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-omk-gateway.sh"

bash -n "${SETUP}"
output="$("${SETUP}" --dry-run --with-soracom --with-ble --with-broute --with-kiosk)"
base_output="$("${SETUP}" --dry-run --with-base)"

grep -Fq 'Base setup: skipped' <<<"${output}"
! grep -Fq 'setup-raspberry-pi.sh' <<<"${output}"
grep -Fq 'setup-raspberry-pi.sh' <<<"${base_output}"
grep -Fq 'setup-soracom-onyx.sh' <<<"${output}"
grep -Fq 'setup-wifi-access-point.sh --activate' <<<"${output}"
grep -Fq 'setup-system-manager.sh' <<<"${output}"
grep -Fq 'setup-data-collection.sh' <<<"${output}"
grep -Fq 'setup-data-transformer.sh' <<<"${output}"
grep -Fq 'setup-ble-sensor-manager.sh' <<<"${output}"
grep -Fq 'setup-broute-meter.sh' <<<"${output}"
grep -Fq 'setup-dashboard-kiosk.sh' <<<"${output}"
! grep -Fq 'setup-ichijo-energy-node.sh' <<<"${output}"

system_manager_line="$(grep -n -F 'setup-system-manager.sh' <<<"${output}" | cut -d: -f1)"
collection_line="$(grep -n -F 'setup-data-collection.sh' <<<"${output}" | cut -d: -f1)"
transformer_line="$(grep -n -F 'setup-data-transformer.sh' <<<"${output}" | cut -d: -f1)"
[[ "${system_manager_line}" -lt "${collection_line}" ]]
[[ "${collection_line}" -lt "${transformer_line}" ]]

# Run in a disposable copy with no-op setup scripts to verify the actual call
# decision, rather than only the displayed dry-run plan.
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/scripts" "${TEMP_DIR}/bin"
cp "${SETUP}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh"
for script in setup-raspberry-pi.sh setup-wifi-access-point.sh setup-system-manager.sh setup-data-collection.sh setup-data-transformer.sh; do
  cat > "${TEMP_DIR}/scripts/${script}" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$(basename "$0")" >> "${CALL_LOG}"
EOF
  chmod +x "${TEMP_DIR}/scripts/${script}"
done
cat > "${TEMP_DIR}/bin/uname" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == '-r' ]] && printf '%s\n' '6.18.39+rpt-rpi-v8' || printf '%s\n' Linux
EOF
cat > "${TEMP_DIR}/bin/id" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == '-nG' ]] && printf '%s\n' docker || printf '%s\n' omkdev
EOF
cat > "${TEMP_DIR}/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${TEMP_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' mosquitto sensor-collector dashboard harvest-uploader
EOF
cat > "${TEMP_DIR}/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${TEMP_DIR}/bin/ip" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'inet 192.168.50.1/24'
EOF
cat > "${TEMP_DIR}/bin/find" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "${TEMP_DIR}/bin/"*

CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh"
! grep -Fq 'setup-raspberry-pi.sh' "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-base
[[ "$(grep -Fxc 'setup-raspberry-pi.sh' "${TEMP_DIR}/calls")" == 1 ]]

echo 'PASS: Gateway orchestrator orders scripts correctly and skips base setup unless explicitly requested.'
