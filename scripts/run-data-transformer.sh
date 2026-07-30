#!/usr/bin/env bash

# Transform the current and previous JST collector files when they exist.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
PYTHON="${OMK_ROOT}/services/data-transformer/.venv/bin/python"
LOCK_FILE="${OMK_ROOT}/data/.data-transformer.lock"

export TZ=Asia/Tokyo
export PYTHONPATH="${OMK_ROOT}/services/data-transformer/src${PYTHONPATH:+:${PYTHONPATH}}"

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

if [[ ! -x "${PYTHON}" ]]; then
  log "ERROR: virtual environment Python is unavailable: ${PYTHON}"
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  log "ERROR: flock is unavailable; install util-linux before running this service."
  exit 1
fi

mkdir -p "${OMK_ROOT}/data"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "Another data-transformer invocation holds ${LOCK_FILE}; skipping this run."
  exit 0
fi

transform_date() {
  local run_date="$1"
  local input_file="${OMK_ROOT}/data/sensors/${run_date:0:4}/${run_date:5:2}/${run_date:8:2}.jsonl"
  local status

  if [[ ! -f "${input_file}" ]]; then
    log "No input for ${run_date}; no work this run: ${input_file}"
    return 0
  fi

  log "Transforming ${run_date}: ${input_file}"
  if "${PYTHON}" -m data_transformer \
    --date "${run_date}" \
    --data-root data/sensors \
    --output data/processed \
    --error-output data/errors/transform; then
    log "Completed ${run_date}."
    return 0
  else
    status=$?
    log "ERROR: Transformation failed for ${run_date} (exit status ${status})."
    return "${status}"
  fi
}

today="$(date +%F)"
yesterday="$(date -d 'yesterday' +%F)"
failures=0

for run_date in "${today}" "${yesterday}"; do
  if ! transform_date "${run_date}"; then
    failures=$((failures + 1))
  fi
done

if ((failures > 0)); then
  log "ERROR: ${failures} date transformation(s) failed; the timer will retry on its next run."
  exit 1
fi

log "Data transformer run finished successfully."
