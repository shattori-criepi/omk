#!/usr/bin/env bash

# Safe, bounded handling for the normal package-manager contention immediately
# after a fresh Raspberry Pi OS boot.  Call as: omk_apt sudo apt-get update
# (or omit sudo when already root).  Never remove lock files or kill processes.

OMK_APT_LOCK_TIMEOUT_SECONDS="${OMK_APT_LOCK_TIMEOUT_SECONDS:-300}"
OMK_APT_LOCK_RETRY_SECONDS="${OMK_APT_LOCK_RETRY_SECONDS:-5}"
OMK_APT_SINGLE_ATTEMPT_TIMEOUT_SECONDS="${OMK_APT_SINGLE_ATTEMPT_TIMEOUT_SECONDS:-30}"

omk_apt_lock_error() {
  grep -Eqi \
    'Could not get lock (/var/lib/dpkg/lock-frontend|/var/lib/dpkg/lock|/var/lib/apt/lists/lock|/var/cache/apt/archives/lock)|Unable to acquire the dpkg frontend lock|Could not get lock.*lock' "$1"
}

omk_apt_lock_holders() {
  local lock_path
  printf '%s\n' 'apt/dpkg lock holders (informational; no process will be stopped):'
  if command -v fuser >/dev/null 2>&1; then
    for lock_path in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do
      [[ -e "${lock_path}" ]] || continue
      fuser -v "${lock_path}" 2>&1 || true
    done
  fi
  ps -eo pid=,comm= 2>/dev/null | grep -E '[a]pt|[d]pkg|[P]ackageKit' || true
}

omk_apt() {
  local started_at="${SECONDS}" elapsed status output
  local -a command=("$@")

  ((${#command[@]} > 0)) || { printf '%s\n' 'ERROR: omk_apt requires an apt-get command.' >&2; return 2; }
  [[ "${OMK_APT_LOCK_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || { printf '%s\n' 'ERROR: OMK_APT_LOCK_TIMEOUT_SECONDS must be a non-negative integer.' >&2; return 2; }
  [[ "${OMK_APT_LOCK_RETRY_SECONDS}" =~ ^[1-9][0-9]*$ ]] || { printf '%s\n' 'ERROR: OMK_APT_LOCK_RETRY_SECONDS must be a positive integer.' >&2; return 2; }
  [[ "${OMK_APT_SINGLE_ATTEMPT_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || { printf '%s\n' 'ERROR: OMK_APT_SINGLE_ATTEMPT_TIMEOUT_SECONDS must be a positive integer.' >&2; return 2; }

  while :; do
    output="$(mktemp)" || return 1
    if "${command[@]}" -o "DPkg::Lock::Timeout=${OMK_APT_SINGLE_ATTEMPT_TIMEOUT_SECONDS}" -o Acquire::Retries=3 2>&1 | tee "${output}"; then
      rm -f -- "${output}"
      return 0
    fi
    status="${PIPESTATUS[0]}"
    if ! omk_apt_lock_error "${output}"; then
      rm -f -- "${output}"
      printf '%s\n' "ERROR: apt-get failed with status ${status}; this is not a lock conflict, so it will not be retried." >&2
      return "${status}"
    fi
    rm -f -- "${output}"
    elapsed="$((SECONDS - started_at))"
    if ((elapsed >= OMK_APT_LOCK_TIMEOUT_SECONDS)); then
      printf '%s\n' "ERROR: apt/dpkg lock remained unavailable for ${elapsed}s (limit ${OMK_APT_LOCK_TIMEOUT_SECONDS}s). Do not remove lock files; wait for the listed normal system task to finish, then rerun setup." >&2
      omk_apt_lock_holders >&2
      return "${status}"
    fi
    printf '%s\n' "INFO: apt/dpkg is busy after fresh OS startup; waiting ${OMK_APT_LOCK_RETRY_SECONDS}s before retry (${elapsed}s/${OMK_APT_LOCK_TIMEOUT_SECONDS}s)." >&2
    omk_apt_lock_holders >&2
    sleep "${OMK_APT_LOCK_RETRY_SECONDS}"
  done
}
