#!/usr/bin/env bash

# Check that the thin Gateway entry point keeps setup work in individual scripts.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-omk-gateway.sh"

bash -n "${SETUP}"
output="$("${SETUP}" --dry-run --with-soracom --with-ble --with-broute)"
base_output="$("${SETUP}" --dry-run --with-base)"
no_kiosk_output="$("${SETUP}" --dry-run --no-kiosk)"

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
grep -Fq 'setup-dashboard-kiosk.sh --prepare' <<<"${base_output}"
! grep -Fq 'setup-dashboard-kiosk.sh' <<<"${no_kiosk_output}"
! grep -Fq 'setup-ichijo-energy-node.sh' <<<"${output}"

system_manager_line="$(grep -n -F 'setup-system-manager.sh' <<<"${output}" | cut -d: -f1)"
collection_line="$(grep -n -F 'setup-data-collection.sh --prepare' <<<"${output}" | cut -d: -f1)"
transformer_line="$(grep -n -F 'setup-data-transformer.sh' <<<"${output}" | cut -d: -f1)"
exporter_line="$(grep -n -F 'setup-data-exporter.sh' <<<"${output}" | cut -d: -f1)"
ap_prepare_line="$(grep -n -F 'setup-wifi-access-point.sh — Required OMK AP profile' <<<"${output}" | cut -d: -f1)"
health_check_line="$(grep -n -F 'final-pre-activation-check — Final local Gateway health verification' <<<"${output}" | cut -d: -f1)"
ap_activation_line="$(grep -n -F 'setup-wifi-access-point.sh --activate' <<<"${output}" | cut -d: -f1)"
start_collection_line="$(grep -n -F 'setup-data-collection.sh — Required Docker collection' <<<"${output}" | cut -d: -f1)"
[[ "${system_manager_line}" -lt "${collection_line}" ]]
[[ "${collection_line}" -lt "${transformer_line}" ]]
[[ "${transformer_line}" -lt "${exporter_line}" ]]
[[ "${exporter_line}" -lt "${ap_prepare_line}" ]]
[[ "${ap_prepare_line}" -lt "${start_collection_line}" ]]
[[ "${start_collection_line}" -lt "${health_check_line}" ]]
[[ "${health_check_line}" -lt "${ap_activation_line}" ]]

# Run in a disposable copy with no-op setup scripts to verify the actual call
# decision, rather than only the displayed dry-run plan.
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/scripts/lib" "${TEMP_DIR}/bin" \
  "${TEMP_DIR}/services/system-manager/.venv/bin" \
  "${TEMP_DIR}/services/data-transformer/.venv/bin" \
  "${TEMP_DIR}/services/mosquitto/config" \
  "${TEMP_DIR}/services/mosquitto/data" \
  "${TEMP_DIR}/data/sensors" "${TEMP_DIR}/data/latest" \
  "${TEMP_DIR}/data/processed" "${TEMP_DIR}/data/dashboard" \
  "${TEMP_DIR}/data/harvest-uploader" "${TEMP_DIR}/data/errors/transform" "${TEMP_DIR}/etc/omk" \
  "${TEMP_DIR}/units" "${TEMP_DIR}/kiosk/default.target.wants"
cp "${SETUP}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh"
cp "${ROOT_DIR}/scripts/lib/apt-helpers.sh" "${TEMP_DIR}/scripts/lib/apt-helpers.sh"
touch "${TEMP_DIR}/compose.yaml" \
  "${TEMP_DIR}/services/mosquitto/config/mosquitto.conf" \
  "${TEMP_DIR}/services/system-manager/.venv/bin/python" \
  "${TEMP_DIR}/services/data-transformer/.venv/bin/python" \
  "${TEMP_DIR}/etc/omk/dashboard-system-manager.env" \
  "${TEMP_DIR}/units/omk-system-manager.service" \
  "${TEMP_DIR}/units/omk-data-transformer.service" \
  "${TEMP_DIR}/units/omk-data-transformer.timer"
chmod +x "${TEMP_DIR}/services/system-manager/.venv/bin/python" "${TEMP_DIR}/services/data-transformer/.venv/bin/python"
touch "${TEMP_DIR}/kiosk/omk-dashboard-kiosk.service"
ln -s ../omk-dashboard-kiosk.service "${TEMP_DIR}/kiosk/default.target.wants/omk-dashboard-kiosk.service"
export OMK_PREFLIGHT_SYSTEMD_UNIT_DIR="${TEMP_DIR}/units"
export OMK_PREFLIGHT_DASHBOARD_ENV_FILE="${TEMP_DIR}/etc/omk/dashboard-system-manager.env"
export OMK_KIOSK_UNIT_PATH="${TEMP_DIR}/kiosk/omk-dashboard-kiosk.service"
export OMK_KIOSK_ENABLE_LINK="${TEMP_DIR}/kiosk/default.target.wants/omk-dashboard-kiosk.service"
export OMK_SKIP_PREBASE_PREFLIGHT=true
for script in setup-raspberry-pi.sh setup-soracom-onyx.sh setup-wifi-access-point.sh setup-system-manager.sh setup-data-collection.sh setup-data-transformer.sh setup-data-exporter.sh setup-ble-sensor-manager.sh setup-broute-meter.sh setup-dashboard-kiosk.sh; do
cat > "${TEMP_DIR}/scripts/${script}" <<'EOF'
#!/usr/bin/env bash
printf '%s %s\n' "$(basename "$0")" "$*" >> "${CALL_LOG}"
if [[ -n "${FAIL_SETUP:-}" && "$(basename "$0")" == "${FAIL_SETUP}" && ( -z "${FAIL_SETUP_ARGS:-}" || "$*" == *"${FAIL_SETUP_ARGS}"* ) ]]; then
  exit 42
fi
EOF
  chmod +x "${TEMP_DIR}/scripts/${script}"
done
cat > "${TEMP_DIR}/bin/uname" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == '-r' ]] && printf '%s\n' '6.18.39+rpt-rpi-v8' || printf '%s\n' Linux
EOF
cat > "${TEMP_DIR}/bin/id" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == '-nG' && "$#" == 1 ]] && printf '%s\n' docker || printf '%s\n' omkdev
EOF
cat > "${TEMP_DIR}/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${BROUTE_INACTIVE:-false}" == true && "$*" == *'omk-broute-meter.service'* ]]; then
  exit 3
fi
exit 0
EOF
cat > "${TEMP_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *'config --quiet'*) [[ "${FAIL_PREFLIGHT_CONFIG:-false}" != true ]] ;;
  *'config --images'*) printf '%s\n' eclipse-mosquitto:2.0.22 omk-sensor-collector omk-dashboard omk-harvest-uploader ;;
  *'image inspect'*) [[ "${FAIL_PREFLIGHT_IMAGE:-false}" != true ]] ;;
  *) printf '%s\n' mosquitto sensor-collector dashboard harvest-uploader ;;
esac
EOF
cat > "${TEMP_DIR}/bin/curl" <<'EOF'
#!/usr/bin/env bash
[[ "${FAIL_HEALTH:-false}" != true ]]
EOF
cat > "${TEMP_DIR}/bin/ip" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'inet 192.168.50.1/24'
EOF
cat > "${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *'connection show omk-ap'* && "$*" == *'--show-secrets'* ]]; then printf '%s\n' test-psk; exit 0; fi
if [[ "$*" == *'connection show omk-ap'* && "$*" == *'-g '* ]]; then
  case "$*" in
    *connection.interface-name*) printf '%s\n' wlan0 ;;
    *802-11-wireless.mode*) printf '%s\n' ap ;;
    *connection.autoconnect*) printf '%s\n' yes ;;
    *ipv4.method*) printf '%s\n' shared ;;
    *ipv4.addresses*) printf '%s\n' 192.168.50.1/24 ;;
    *ipv6.method*) printf '%s\n' disabled ;;
  esac
fi
exit 0
EOF
cat > "${TEMP_DIR}/bin/find" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${TEMP_DIR}/bin/systemd-analyze" <<'EOF'
#!/usr/bin/env bash
[[ "${FAIL_PREFLIGHT_SYSTEMD:-false}" != true ]]
EOF
chmod +x "${TEMP_DIR}/bin/"*

CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh"
! grep -Fq 'setup-raspberry-pi.sh' "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-base
[[ "$(grep -Fxc 'setup-raspberry-pi.sh ' "${TEMP_DIR}/calls")" == 1 ]]

# A named-user group lookup must not bypass the re-login boundary: a process
# running through sudo has not acquired the newly added docker group yet.
rm -f -- "${TEMP_DIR}/calls"
SUDO_USER=omkdev CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-base >"${TEMP_DIR}/relogin-required.log"
grep -Fq 'Docker group membership needs a new login session.' "${TEMP_DIR}/relogin-required.log"
! grep -Fq 'setup-system-manager.sh' "${TEMP_DIR}/calls"

# A base setup failure, including an exhausted apt/dpkg lock timeout, must not
# let the top-level orchestrator reach AP activation.
rm -f -- "${TEMP_DIR}/calls"
if FAIL_SETUP=setup-raspberry-pi.sh CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-base >"${TEMP_DIR}/base-failed.log" 2>&1; then
  echo 'Gateway setup accepted a failed base setup.' >&2
  exit 1
fi
grep -Fq 'setup-raspberry-pi.sh ' "${TEMP_DIR}/calls"
! grep -Fq 'setup-wifi-access-point.sh' "${TEMP_DIR}/calls"

rm -f -- "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-soracom --with-ble --with-broute
line_number() { grep -n -F "$1" "${TEMP_DIR}/calls" | head -n 1 | cut -d: -f1; }
ap_line="$(line_number 'setup-wifi-access-point.sh')"
for script in \
  'setup-soracom-onyx.sh ' \
  'setup-system-manager.sh ' \
  'setup-data-collection.sh --prepare' \
  'setup-data-transformer.sh ' \
  'setup-data-exporter.sh ' \
  'setup-ble-sensor-manager.sh ' \
  'setup-broute-meter.sh '; do
  [[ "$(line_number "${script}")" -lt "${ap_line}" ]]
done
start_collection_line="$(grep -n -E 'setup-data-collection\.sh $' "${TEMP_DIR}/calls" | cut -d: -f1)"
[[ "${ap_line}" -lt "${start_collection_line}" ]]
normal_kiosk_line="$(grep -n -E 'setup-dashboard-kiosk\.sh $' "${TEMP_DIR}/calls" | cut -d: -f1)"
[[ "${start_collection_line}" -lt "${normal_kiosk_line}" ]]
activation_line="$(line_number 'setup-wifi-access-point.sh --activate')"
[[ "${normal_kiosk_line}" -lt "${activation_line}" ]]
[[ "$(tail -n 1 "${TEMP_DIR}/calls")" == 'setup-wifi-access-point.sh --activate' ]]

# A missing B-route adapter or credentials must not turn a completed Gateway
# installation into an overall failure when the optional service is inactive.
rm -f -- "${TEMP_DIR}/calls"
BROUTE_INACTIVE=true CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --with-broute >"${TEMP_DIR}/broute-inactive.log"
grep -Fq 'WARN: B-route meter is installed and enabled but not active.' "${TEMP_DIR}/broute-inactive.log"

# A required preparation failure must stop before wlan0 is changed to the AP.
rm -f -- "${TEMP_DIR}/calls"
if FAIL_SETUP=setup-data-collection.sh CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/prepare-failed.log" 2>&1; then
  echo 'Gateway setup accepted a failed Docker preparation step.' >&2
  exit 1
fi
grep -Fq 'setup-data-collection.sh --prepare' "${TEMP_DIR}/calls"
! grep -Fq 'setup-wifi-access-point.sh' "${TEMP_DIR}/calls"

# Full Dashboard/kiosk and health failures must stop before final activation.
for failure_setup in setup-dashboard-kiosk.sh setup-data-collection.sh; do
  rm -f -- "${TEMP_DIR}/calls"
  if FAIL_SETUP="${failure_setup}" FAIL_SETUP_ARGS='' CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
    "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/${failure_setup}.log" 2>&1; then
    echo "Gateway setup accepted failed ${failure_setup}." >&2
    exit 1
  fi
  ! grep -Fq 'setup-wifi-access-point.sh --activate' "${TEMP_DIR}/calls"
done
rm -f -- "${TEMP_DIR}/calls"
if FAIL_HEALTH=true CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/health-failed.log" 2>&1; then
  echo 'Gateway setup accepted failed Dashboard health.' >&2
  exit 1
fi
! grep -Fq 'setup-wifi-access-point.sh --activate' "${TEMP_DIR}/calls"

# Failure of the last activation remains incomplete rather than a false
# overall SUCCESS; the non-activate profile preparation has already run.
rm -f -- "${TEMP_DIR}/calls"
if FAIL_SETUP=setup-wifi-access-point.sh FAIL_SETUP_ARGS=--activate CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/activation-failed.log" 2>&1; then
  echo 'Gateway setup accepted failed final AP activation.' >&2
  exit 1
fi
grep -Fq 'setup-wifi-access-point.sh ' "${TEMP_DIR}/calls"
grep -Fq 'setup-wifi-access-point.sh --activate' "${TEMP_DIR}/calls"
grep -Fq 'final activation was not completed' "${TEMP_DIR}/activation-failed.log"

# Every preflight failure is before wlan0 is handed to the AP script.
for failure in FAIL_PREFLIGHT_CONFIG FAIL_PREFLIGHT_IMAGE FAIL_PREFLIGHT_SYSTEMD; do
  rm -f -- "${TEMP_DIR}/calls"
  if env "${failure}=true" CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
    "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/${failure}.log" 2>&1; then
    echo "Gateway setup accepted failed ${failure}." >&2
    exit 1
  fi
  grep -Fq 'AP preflight failed:' "${TEMP_DIR}/${failure}.log"
  ! grep -Fq 'setup-wifi-access-point.sh' "${TEMP_DIR}/calls"
done

# Simulate a missing post-AP command by making the docker command itself
# unavailable. The preparation scripts are stubs, so this reaches preflight.
mkdir -p "${TEMP_DIR}/bin-no-docker"
for command_name in bash basename dirname id uname find systemctl systemd-analyze curl ip grep tr sed sort tail; do
  command_path="${TEMP_DIR}/bin/${command_name}"
  if [[ -x "${command_path}" ]]; then
    ln -sf "${command_path}" "${TEMP_DIR}/bin-no-docker/${command_name}"
  else
    ln -sf "$(command -v "${command_name}")" "${TEMP_DIR}/bin-no-docker/${command_name}"
  fi
done
rm -f -- "${TEMP_DIR}/calls"
if CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin-no-docker" \
  /usr/bin/bash "${TEMP_DIR}/scripts/setup-omk-gateway.sh" >"${TEMP_DIR}/missing-command.log" 2>&1; then
  echo 'Gateway setup accepted a missing docker command.' >&2
  exit 1
fi
grep -Fq 'required post-AP command is unavailable: docker' "${TEMP_DIR}/missing-command.log"
! grep -Fq 'setup-wifi-access-point.sh' "${TEMP_DIR}/calls"

# The kiosk package setup must occur before the common AP preflight and AP
# activation; its normal GUI work remains after local Compose startup.
rm -f -- "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh"
[[ "$(line_number 'setup-dashboard-kiosk.sh --prepare')" -lt "$(line_number 'setup-wifi-access-point.sh')" ]]
normal_kiosk_line="$(grep -n -E 'setup-dashboard-kiosk\.sh $' "${TEMP_DIR}/calls" | cut -d: -f1)"
[[ "$(line_number 'setup-wifi-access-point.sh')" -lt "${normal_kiosk_line}" ]]

rm -f -- "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --no-kiosk
! grep -Fq 'setup-dashboard-kiosk.sh' "${TEMP_DIR}/calls"
[[ "$(tail -n 1 "${TEMP_DIR}/calls")" == 'setup-wifi-access-point.sh --activate' ]]

# A rerun keeps the same profile-prepare, local-startup, health, and final
# activation decision sequence; it does not activate earlier on the second run.
cp "${TEMP_DIR}/calls" "${TEMP_DIR}/calls.no-kiosk.first"
rm -f -- "${TEMP_DIR}/calls"
CALL_LOG="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  "${TEMP_DIR}/scripts/setup-omk-gateway.sh" --no-kiosk
cmp "${TEMP_DIR}/calls.no-kiosk.first" "${TEMP_DIR}/calls"

echo 'PASS: Gateway orchestrator orders scripts correctly and skips base setup unless explicitly requested.'
