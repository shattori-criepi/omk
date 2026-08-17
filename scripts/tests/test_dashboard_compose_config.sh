#!/usr/bin/env bash

# This is a repository-level Compose contract test. It intentionally remains
# outside the Dashboard image: production images only contain /app/app and
# /app/tests, not the repository root or compose.yaml.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
COMPOSE_FILE="${ROOT_DIR}/compose.yaml"
DASHBOARD_SECTION="$(sed -n '/^  dashboard:/,/^  sensor-collector:/p' "${COMPOSE_FILE}")"

grep -Fq 'OMK_SYSTEM_MANAGER_URL: http://host.docker.internal:8788' <<<"${DASHBOARD_SECTION}"
grep -Fq -- '- /etc/omk/dashboard-system-manager.env' <<<"${DASHBOARD_SECTION}"
if grep -Fq 'credentials.yaml' <<<"${DASHBOARD_SECTION}"; then
  echo 'Dashboard Compose section must not mount credentials.yaml.' >&2
  exit 1
fi

echo 'PASS: Dashboard Compose configuration keeps credentials out of the container.'
