#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP_SCRIPT="${ROOT_DIR}/scripts/setup-broute-meter.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# shellcheck source=/dev/null
source "${SETUP_SCRIPT}"

OMK_ROOT="${TEMP_DIR}/omk"
TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
SUDO=()
DRY_RUN=false

legacy_config="${OMK_ROOT}/broute-meter/config"
current_config="${OMK_ROOT}/services/broute-meter/config"
mkdir -p "${legacy_config}" "${current_config}"

legacy_secret='legacy-secret-must-not-appear-in-output'
cat > "${legacy_config}/credentials.yaml" <<EOF
b_route:
  id: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  password: "${legacy_secret}"
EOF
cat > "${legacy_config}/settings.yaml" <<'EOF'
storage:
  data_directory: "../data/broute-meter"
logging:
  directory: "../logs/broute-meter"
serial:
  port: /dev/ttyUSB0
EOF

output="$(migrate_legacy_runtime_config)"
[[ -f "${current_config}/credentials.yaml" ]]
[[ -f "${current_config}/settings.yaml" ]]
[[ "$(stat -c '%a' "${current_config}/credentials.yaml")" == 600 ]]
[[ "$(stat -c '%a' "${current_config}/settings.yaml")" == 600 ]]
grep -Fxq '  data_directory: "../../data/broute-meter"' "${current_config}/settings.yaml"
grep -Fxq '  directory: "../../logs/broute-meter"' "${current_config}/settings.yaml"
grep -Fxq '  port: /dev/ttyUSB0' "${current_config}/settings.yaml"
! grep -Fq "${legacy_secret}" <<<"${output}"

cat > "${current_config}/credentials.yaml" <<'EOF'
b_route:
  id: "NEW-CREDENTIALS-MUST-STAY"
EOF
cat > "${current_config}/settings.yaml" <<'EOF'
storage:
  data_directory: "/current/data"
EOF
output="$(migrate_legacy_runtime_config)"
grep -Fqx '  id: "NEW-CREDENTIALS-MUST-STAY"' "${current_config}/credentials.yaml"
grep -Fqx '  data_directory: "/current/data"' "${current_config}/settings.yaml"
! grep -Fq "${legacy_secret}" <<<"${output}"

rm -f -- "${current_config}/settings.yaml"
cat > "${legacy_config}/settings.yaml" <<'EOF'
storage:
  data_directory: "/srv/omk/data"
logging:
  directory: "./custom-logs"
EOF
migrate_legacy_runtime_config >/dev/null
grep -Fqx '  data_directory: "/srv/omk/data"' "${current_config}/settings.yaml"
grep -Fqx '  directory: "./custom-logs"' "${current_config}/settings.yaml"

echo 'PASS: B-route legacy runtime config migration is safe and path-aware.'
