#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
source "${ROOT}/scripts/lib/python-src-import-smoke.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

cat >"${TEMP_DIR}/as-target" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat >"${TEMP_DIR}/python" <<'EOF'
#!/usr/bin/env bash
[[ -n "${PYTHONPATH:-}" && -n "${OMK_IMPORT_SMOKE_SRC:-}" && -n "${OMK_IMPORT_SMOKE_MODULE:-}" ]]
[[ "${PYTHONPATH}" == "${OMK_IMPORT_SMOKE_SRC}" ]]
[[ -f "${PYTHONPATH}/${OMK_IMPORT_SMOKE_MODULE}/__init__.py" ]]
[[ "${1:-}" == -c && "${2:-}" == *'importlib.import_module'* ]]
printf '%s|%s\n' "${OMK_IMPORT_SMOKE_MODULE}" "${PYTHONPATH}" >>"${SMOKE_CALL_LOG}"
EOF
chmod +x "${TEMP_DIR}/as-target" "${TEMP_DIR}/python"
export SMOKE_CALL_LOG="${TEMP_DIR}/calls"

run_smoke_twice() {
  local source_dir="$1" module_name="$2"
  omk_python_src_import_smoke "${source_dir}" "${module_name}" "${TEMP_DIR}/python" "${TEMP_DIR}/as-target"
  # Simulates rerunning setup with its venv Python already present.
  omk_python_src_import_smoke "${source_dir}" "${module_name}" "${TEMP_DIR}/python" "${TEMP_DIR}/as-target"
  [[ "$(grep -Fc "${module_name}|${source_dir}" "${SMOKE_CALL_LOG}")" == 2 ]]
}

run_smoke_twice "${ROOT}/services/system-manager/src" omk_system_manager
run_smoke_twice "${ROOT}/services/data-transformer/src" data_transformer
run_smoke_twice "${ROOT}/services/data-exporter/src" data_exporter

# All three production setup scripts must use the shared runtime-src helper.
grep -Fq 'python-src-import-smoke.sh' "${ROOT}/scripts/setup-system-manager.sh"
grep -Fq 'services/system-manager/src" omk_system_manager' "${ROOT}/scripts/setup-system-manager.sh"
grep -Fq 'python-src-import-smoke.sh' "${ROOT}/scripts/setup-data-transformer.sh"
grep -Fq 'services/data-transformer/src" data_transformer' "${ROOT}/scripts/setup-data-transformer.sh"
grep -Fq 'python-src-import-smoke.sh' "${ROOT}/scripts/setup-data-exporter.sh"
grep -Fq 'services/data-exporter/src" data_exporter' "${ROOT}/scripts/setup-data-exporter.sh"

echo 'PASS: src-layout smoke imports use runtime PYTHONPATH, verify module origin, and are rerunnable.'
