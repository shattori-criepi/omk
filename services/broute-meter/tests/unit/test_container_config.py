"""Dockerなしで最小コンテナ設定の安全な既定値を検証する。"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OMK_ROOT = PROJECT_ROOT.parents[1]


def test_development_compose_service_uses_mock_without_usb_or_credentials() -> None:
    compose = yaml.safe_load(
        (OMK_ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    mock_service = services["broute-meter-mock"]

    assert mock_service["build"]["target"] == "runtime"
    assert mock_service["command"][-2:] == ["setup-adapter", "--mock"]
    assert "profiles" not in mock_service
    assert mock_service["network_mode"] == "none"
    assert mock_service["read_only"] is True

    assert mock_service["environment"] == {
        "OMK_DATA_DIR": "/data",
        "OMK_LOG_DIR": "/logs",
    }
    assert mock_service["volumes"] == [
        "./data/broute-meter:/data",
        "./logs/broute-meter:/logs",
    ]
    assert mock_service["user"] == "${OMK_UID:-1000}:${OMK_GID:-1000}"

    test_service = services["broute-meter-tests"]
    assert test_service["build"]["target"] == "test"
    assert test_service["command"][0] == "tests/integration"
    assert test_service["profiles"] == ["test"]

    for service in (mock_service, test_service):
        for forbidden_key in (
            "devices",
            "env_file",
            "secrets",
        ):
            assert forbidden_key not in service
        if service is not mock_service:
            assert "environment" not in service
            assert "volumes" not in service
        assert service.get("privileged", False) is False
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]


def test_real_credentials_and_local_outputs_are_excluded_from_build_context() -> None:
    ignored = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".env",
        "config/settings.yaml",
        "config/credentials.yaml",
        "data/",
        "logs/",
    } <= ignored


def test_runtime_image_uses_python_312_and_non_root_user() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim AS base" in dockerfile
    assert "FROM base AS test" in dockerfile
    assert "FROM base AS runtime" in dockerfile
    assert dockerfile.rstrip().splitlines()[-2] == (
        'ENTRYPOINT ["python", "-m", "broute_meter"]'
    )
    assert "USER 10001:10001" in dockerfile
