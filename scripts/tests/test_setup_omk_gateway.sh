#!/usr/bin/env bash

# Check that the thin Gateway entry point keeps setup work in individual scripts.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-omk-gateway.sh"

bash -n "${SETUP}"
output="$("${SETUP}" --dry-run --with-soracom --with-ble --with-broute --with-kiosk)"

grep -Fq 'setup-raspberry-pi.sh' <<<"${output}"
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

echo 'PASS: Gateway orchestrator orders required and optional setup scripts correctly.'
