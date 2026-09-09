#!/usr/bin/env bash
# Sourced by setup-data-collection. No profile or NetworkManager operations.
prepare_publish_rollback() {
  local names name
  local -a containers=()
  names="$("${DOCKER_CMD[@]}" ps -a --format '{{.Names}}')" || return 1
  for name in "${PRODUCTION_SERVICES[@]}"; do
    name="omk-${name}"
    if grep -Fxq "${name}" <<<"${names}"; then containers+=("${name}"); fi
  done
  PORT_ROLLBACK="$(mktemp)" || return 1
  IMAGE_ROLLBACK="$(mktemp)" || return 1
  chmod 0600 "${PORT_ROLLBACK}" "${IMAGE_ROLLBACK}" || return 1
  if ((${#containers[@]})); then
    "${DOCKER_CMD[@]}" inspect "${containers[@]}" |
      python3 "${SCRIPT_DIR}/lib/legacy-compose-ports.py" "${IMAGE_ROLLBACK}" >"${PORT_ROLLBACK}" || return 1
  fi
  if [[ -s "${PORT_ROLLBACK}" ]]; then
    # Validate rollback syntax before changing running containers.
    docker_compose -f "${PORT_ROLLBACK}" config --quiet || return 1
    FIREWALL_ROLLBACK="$(mktemp)" || return 1
    chmod 0600 "${FIREWALL_ROLLBACK}" || return 1
    local -a privilege=()
    ((EUID == 0)) || privilege=(sudo)
    # Preserve the currently working legacy forwarding boundary as well as ports.
    printf 'add table inet omk_ap_isolation\ndelete table inet omk_ap_isolation\n' >"${FIREWALL_ROLLBACK}"
    "${privilege[@]}" nft list table inet omk_ap_isolation >>"${FIREWALL_ROLLBACK}" || return 1
  fi
}
verify_restored_production() {
  local rows service image host actual running attempt ready
  rows="$(python3 - "${IMAGE_ROLLBACK}" <<'PYCODE'
import json, sys
for service, saved in json.load(open(sys.argv[1])).items():
    print(service, saved['image'], saved['host'])
PYCODE
)" || return 1
  while read -r service image host; do
    actual="$("${DOCKER_CMD[@]}" inspect -f '{{.Image}}' "omk-${service}")" || return 1
    [[ "${actual}" == "${image}" ]] || { log "ERROR: rollback image mismatch for ${service}."; return 1; }
    running="$("${DOCKER_CMD[@]}" inspect -f '{{.State.Running}}' "omk-${service}")" || return 1
    [[ "${running}" == true ]] || return 1
    ready=no
    for attempt in 1 2 3 4 5; do
      case "${service}" in
        dashboard) curl --fail --silent --show-error --max-time 5 "http://${host}:8000/health" >/dev/null && ready=yes ;;
        mosquitto) python3 "${SCRIPT_DIR}/lib/check-mqtt-backend.py" "${host}" && ready=yes ;;
        *) ready=yes ;;
      esac
      [[ "${ready}" == yes ]] && break
      sleep 2
    done
    [[ "${ready}" == yes ]] || { log "ERROR: rollback backend health failed for ${service}."; return 1; }
    log "PASS: rollback restored ${service} at immutable image ${image}."
  done <<<"${rows}"
}

restore_old_production() {
  local saved_services
  local -a privilege=() restored=()
  ((EUID == 0)) || privilege=(sudo)
  saved_services="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))))' "${IMAGE_ROLLBACK}")" || return 1
  mapfile -t restored <<<"${saved_services}"
  [[ -n "${restored[0]:-}" ]] || return 1
  # Release both new paths before restoring the captured old bindings.
  "${privilege[@]}" systemctl stop omk-dashboard-ap-proxy.service omk-mqtt-ap-proxy.service \
    omk-dashboard-ap-proxy.socket omk-mqtt-ap-proxy.socket || return 1
  "${privilege[@]}" systemctl disable omk-dashboard-ap-proxy.socket omk-mqtt-ap-proxy.socket || return 1
  docker_compose stop "${PRODUCTION_SERVICES[@]}" || return 1
  # --no-deps avoids starting a service that was not running in the snapshot.
  docker_compose -f "${PORT_ROLLBACK}" up -d --no-build --pull never --no-deps "${restored[@]}" || return 1
  if [[ -s "${FIREWALL_ROLLBACK:-}" ]]; then
    "${privilege[@]}" nft -f "${FIREWALL_ROLLBACK}" || return 1
    "${privilege[@]}" install -o root -g root -m 0644 "${FIREWALL_ROLLBACK}" /etc/omk/omk-ap-isolation.nft || return 1
  fi
  verify_restored_production || return 1
}

finish_publish_migration() {
  local status="$1"
  trap - EXIT
  trap '' TERM INT HUP
  if ((status != 0)) && [[ "${MIGRATION_STARTED:-no}" == yes && -s "${PORT_ROLLBACK:-}" ]]; then
    log 'ERROR: migration failed; restoring previous production images and AP publishes without cycling the AP.'
    if restore_old_production; then
      log 'Rollback verified: previous production image IDs and backend health restored. Migration remains failed.'
    else
      log "ERROR: rollback failed; migration also failed (status ${status}). Recovery snapshots retained: ${PORT_ROLLBACK} ${IMAGE_ROLLBACK} ${FIREWALL_ROLLBACK}"
      return "${status}"
    fi
  fi
  rm -f -- "${PORT_ROLLBACK:-}" "${IMAGE_ROLLBACK:-}" "${FIREWALL_ROLLBACK:-}"
  return "${status}"
}
