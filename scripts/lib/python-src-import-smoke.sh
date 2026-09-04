#!/usr/bin/env bash

# Run a src-layout import check through the same unprivileged execution path
# used by setup scripts. The module origin check prevents an unrelated
# site-packages installation with the same name from masking a missing src
# path.

omk_python_src_import_smoke() {
  local source_dir="$1" module_name="$2" python_path="$3"
  shift 3

  [[ -d "${source_dir}/${module_name}" ]] || {
    printf 'ERROR: source package is unavailable: %s/%s\n' "${source_dir}" "${module_name}" >&2
    return 1
  }
  [[ -x "${python_path}" ]] || {
    printf 'ERROR: virtual environment Python is unavailable: %s\n' "${python_path}" >&2
    return 1
  }
  command -v env >/dev/null 2>&1 || {
    printf '%s\n' 'ERROR: env is required for the src-layout import smoke test.' >&2
    return 1
  }

  "$@" env "PYTHONPATH=${source_dir}" "OMK_IMPORT_SMOKE_SRC=${source_dir}" \
    "OMK_IMPORT_SMOKE_MODULE=${module_name}" "${python_path}" -c '
import importlib
import os

module = importlib.import_module(os.environ["OMK_IMPORT_SMOKE_MODULE"])
source_dir = os.path.realpath(os.environ["OMK_IMPORT_SMOKE_SRC"])
module_file = os.path.realpath(module.__file__ or "")
if os.path.commonpath((source_dir, module_file)) != source_dir:
    raise RuntimeError(f"imported {module.__name__} outside runtime src directory: {module_file}")
'
}
