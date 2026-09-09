#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEMP_DIR="$(mktemp -d)"
trap '[[ "${KEEP_TEST_TEMP:-no}" == yes ]] || rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/scripts/lib" "${TEMP_DIR}/bin" "${TEMP_DIR}/systemd" \
  "${TEMP_DIR}/services/mosquitto/config" "${TEMP_DIR}/services/sensor-collector" \
  "${TEMP_DIR}/services/dashboard" "${TEMP_DIR}/services/harvest-uploader/src/harvest_uploader" \
  "${TEMP_DIR}/data/sensors" "${TEMP_DIR}/data/latest" "${TEMP_DIR}/data/processed" \
  "${TEMP_DIR}/data/dashboard" "${TEMP_DIR}/data/harvest-uploader" \
  "${TEMP_DIR}/services/mosquitto/data" "${TEMP_DIR}/logs/setup"
cp "${ROOT_DIR}/scripts/setup-data-collection.sh" "${TEMP_DIR}/scripts/"
cp "${ROOT_DIR}/scripts/lib/validate-compose-publishes.py" "${TEMP_DIR}/scripts/lib/"
cp "${ROOT_DIR}/scripts/lib/validate-ap-socket-units.sh" "${TEMP_DIR}/scripts/lib/"
cp "${ROOT_DIR}/scripts/lib/validate-ap-socket-units.py" "${TEMP_DIR}/scripts/lib/"
cp "${ROOT_DIR}"/systemd/omk-*-ap-proxy.*.in "${TEMP_DIR}/systemd/"
cp "${ROOT_DIR}/compose.yaml" "${TEMP_DIR}/"
touch "${TEMP_DIR}/services/mosquitto/config/mosquitto.conf" \
  "${TEMP_DIR}/services/sensor-collector/Dockerfile" "${TEMP_DIR}/services/dashboard/Dockerfile" \
  "${TEMP_DIR}/services/harvest-uploader/Dockerfile" "${TEMP_DIR}/services/harvest-uploader/requirements.txt" \
  "${TEMP_DIR}/services/harvest-uploader/src/harvest_uploader/__main__.py"

cat >"${TEMP_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${DOCKER_CALLS}"
if [[ "$*" == *'--format json'* ]]; then
  printf '%s\n' '{"services":{"mosquitto":{"ports":[{"target":1883,"published":"1883","host_ip":"127.0.0.1","protocol":"tcp"}]},"dashboard":{"ports":[{"target":8000,"published":"8000","host_ip":"127.0.0.1","protocol":"tcp"}]}}}'
fi
EOF
cat >"${TEMP_DIR}/bin/ip" <<'EOF'
#!/usr/bin/env bash
echo 'AP address was queried during backend preparation.' >&2
exit 99
EOF
chmod +x "${TEMP_DIR}/bin/"*

DOCKER_CALLS="${TEMP_DIR}/docker.calls" PATH="${TEMP_DIR}/bin:${PATH}" \
  bash "${TEMP_DIR}/scripts/setup-data-collection.sh" --prepare >"${TEMP_DIR}/prepare.log" 2>&1
grep -Fq 'pull mosquitto' "${TEMP_DIR}/docker.calls"
grep -Fq 'build --pull' "${TEMP_DIR}/docker.calls"

for template in "${TEMP_DIR}"/systemd/omk-*-ap-proxy.*.in; do
  sed 's|@SYSTEMD_SOCKET_PROXYD@|/usr/lib/systemd/systemd-socket-proxyd|g' "${template}" >"${TEMP_DIR}/$(basename "${template}" .in)"
done
"${TEMP_DIR}/scripts/lib/validate-ap-socket-units.sh" "${TEMP_DIR}" >/dev/null

echo 'PASS: image preparation and socket source validation do not require an AP address (runtime covered by test_ap_publish_migration.sh).'
