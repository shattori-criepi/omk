from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_system_manager_unit_allows_the_narrow_sudoers_escalation() -> None:
    unit = (REPOSITORY_ROOT / "systemd/omk-system-manager.service.in").read_text(
        encoding="utf-8"
    )

    assert "NoNewPrivileges=true" not in unit
    assert "User=@OMK_USER@" in unit
    assert "Group=@OMK_GROUP@" in unit
    assert "PrivateTmp=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ReadWritePaths=@OMK_ROOT@/services/broute-meter/config @OMK_ROOT@/data/broute-meter @OMK_ROOT@/data/site @OMK_ROOT@/data -/run/omk-export-usb -/media/@OMK_USER@" in unit
    assert "Environment=OMK_BROUTE_STATUS_PATH=@OMK_ROOT@/data/broute-meter/status.json" in unit
    assert "Environment=OMK_BROUTE_RETRY_REQUEST_PATH=@OMK_ROOT@/data/broute-meter/retry-request" in unit
    assert "Environment=OMK_SITE_UUID_PATH=@OMK_ROOT@/data/site/site_uuid" in unit


def test_setup_reloads_and_restarts_active_system_manager() -> None:
    setup = (REPOSITORY_ROOT / "scripts/setup-system-manager.sh").read_text(
        encoding="utf-8"
    )

    assert 'systemctl daemon-reload' in setup
    assert 'systemctl enable "${SERVICE_NAME}"' in setup
    assert 'systemctl is-active --quiet "${SERVICE_NAME}"' in setup
    assert 'systemctl restart "${SERVICE_NAME}"' in setup
    assert 'systemctl start "${SERVICE_NAME}"' in setup


def test_setup_preserves_and_validates_a_root_only_existing_token() -> None:
    setup = (REPOSITORY_ROOT / "scripts/setup-system-manager.sh").read_text(
        encoding="utf-8"
    )

    # The token's existence check must be privileged because the installed
    # environment file is root:root 0600. The generation branch follows the
    # existing-file branch, so rerunning setup does not rotate a valid token.
    existing_branch = setup.index('if [[ -e "${ENV_FILE}" ]]; then')
    generation = setup.index('token="$(openssl rand -hex 32)"')
    assert existing_branch < generation
    assert '"${SUDO[@]}" grep -q \'^OMK_SYSTEM_MANAGER_TOKEN=.\' "${ENV_FILE}"' in setup
    assert 'log "Preserving existing root-only token file."' in setup
    assert 'DASHBOARD_ENV_FILE="${ENV_DIR}/dashboard-system-manager.env"' in setup
    assert 'install -o root -g "${TARGET_GROUP}" -m 0640 "${ENV_FILE}" "${DASHBOARD_ENV_FILE}"' in setup
    assert '%s reboot, %s poweroff' in setup
    assert '"${TARGET_USER}" "${SYSTEMCTL_PATH}" "${SYSTEMCTL_PATH}" "${SYSTEMCTL_PATH}" "${SYSTEMCTL_PATH}"' in setup


def test_setup_installs_the_host_mqtt_client_used_for_usb_node_provisioning() -> None:
    setup = (REPOSITORY_ROOT / "scripts/setup-system-manager.sh").read_text(
        encoding="utf-8"
    )
    assert "network-manager mosquitto-clients" in setup
