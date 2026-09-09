#!/usr/bin/env bash

# Print yes only when the official setup is needed to create a missing profile.
soracom_official_setup_required() {
  local profile_exists="$1" cellular_active="$2" dispatcher_exists="$3"
  if [[ "${cellular_active}" == yes && "${profile_exists}" != yes ]]; then
    printf 'invalid\n'
    return 2
  fi
  if [[ "${profile_exists}" == yes ]]; then
    printf 'no\n'
  else
    printf 'yes\n'
  fi
}
