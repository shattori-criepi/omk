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
    assert "ReadWritePaths=@OMK_ROOT@/broute-meter/config" in unit


def test_setup_reloads_and_restarts_active_system_manager() -> None:
    setup = (REPOSITORY_ROOT / "scripts/setup-system-manager.sh").read_text(
        encoding="utf-8"
    )

    assert 'systemctl daemon-reload' in setup
    assert 'systemctl enable "${SERVICE_NAME}"' in setup
    assert 'systemctl is-active --quiet "${SERVICE_NAME}"' in setup
    assert 'systemctl restart "${SERVICE_NAME}"' in setup
    assert 'systemctl start "${SERVICE_NAME}"' in setup
