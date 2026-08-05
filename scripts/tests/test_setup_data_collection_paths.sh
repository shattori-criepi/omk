#!/usr/bin/env bash

# Verify the Harvest mount check derives its directory from the queue path.
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-data-collection.sh"
CAPTURE_FILE="$(mktemp)"
trap 'rm -f -- "${CAPTURE_FILE}"' EXIT

# Load only the small function under test; do not run the setup script's main flow.
# shellcheck disable=SC1090
source <(sed -n '/^check_harvest_queue_mount()/,/^}$/p' "${SCRIPT_PATH}")

HARVEST_QUEUE_CONTAINER_PATH='/app/data/harvest-uploader/queue.sqlite3'
HARVEST_QUEUE_CONTAINER_DIR="${HARVEST_QUEUE_CONTAINER_PATH%/*}"
docker_compose() { printf '%q ' "$@" >> "${CAPTURE_FILE}"; printf '\n' >> "${CAPTURE_FILE}"; }
fail() { return 1; }
log() { :; }

check_harvest_queue_mount

grep -Fq 'test\ -w\ /app/data/harvest-uploader' "${CAPTURE_FILE}"
grep -Fq 'queue.sqlite3' "${CAPTURE_FILE}"
if grep -Eq '/app/data/harvest-uploade([^r]|$)' "${CAPTURE_FILE}"; then
  echo 'Harvest mount check contains the misspelled path.' >&2
  exit 1
fi

echo 'PASS: Harvest mount check uses the queue path parent directory.'
