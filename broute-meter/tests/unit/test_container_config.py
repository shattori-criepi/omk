"""Dockerなしで最小コンテナ設定の安全な既定値を検証する。"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_default_compose_service_uses_mock_without_usb_or_credentials() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    mock_service = services["broute-meter-mock"]

    assert mock_service["build"]["target"] == "runtime"
    assert mock_service["command"][-2:] == ["setup-adapter", "--mock"]
    assert "profiles" not in mock_service
    assert mock_service["network_mode"] == "none"
    assert mock_service["read_only"] is True

    test_service = services["tests"]
    assert test_service["build"]["target"] == "test"
    assert test_service["command"][0] == "tests/integration"
    assert test_service["profiles"] == ["test"]

    for service in services.values():
        for forbidden_key in (
            "devices",
            "env_file",
            "environment",
            "secrets",
            "volumes",
        ):
            assert forbidden_key not in service
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
