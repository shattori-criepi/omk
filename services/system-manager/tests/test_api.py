from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omk_system_manager.main import Settings, create_app
from omk_system_manager.service_control import ServiceControlError

VALID_ID = "A" * 32
VALID_PASSWORD = "B" * 12
TOKEN = "test-token"


class ActiveController:
    def __init__(self, *, failure: bool = False, active: bool = True) -> None:
        self.failure = failure
        self.active = active
        self.restart_called = False

    def is_active(self) -> bool:
        return self.active

    def restart_and_verify(self) -> None:
        self.restart_called = True
        if self.failure:
            raise ServiceControlError("credentials_saved_restart_failed")


def client_for(tmp_path: Path) -> TestClient:
    app = create_app(Settings(TOKEN, tmp_path / "credentials.yaml", "/usr/bin/systemctl"))
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_token_is_required_and_checked(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get("/api/broute/credentials/status").status_code == 401
        assert client.get("/api/broute/credentials/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_update_returns_no_password_or_raw_id_and_logs_no_secret(tmp_path: Path, caplog) -> None:
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        with caplog.at_level(logging.DEBUG):
            response = client.put(
                "/api/broute/credentials",
                headers=headers(),
                json={"id": VALID_ID, "password": VALID_PASSWORD},
            )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "id_masked": "AAAA************************AAAA",
        "password_configured": True,
        "service_active": True,
    }
    combined = response.text + caplog.text
    assert VALID_ID not in combined
    assert VALID_PASSWORD not in combined


def test_invalid_input_returns_400_without_secret(tmp_path: Path) -> None:
    invalid_id = "not-a-valid-id"
    invalid_password = "not-a-valid-password"
    with client_for(tmp_path) as client:
        response = client.put(
            "/api/broute/credentials",
            headers=headers(),
            json={"id": invalid_id, "password": invalid_password},
        )
    assert response.status_code == 400
    assert invalid_id not in response.text
    assert invalid_password not in response.text


def test_request_repr_does_not_contain_credentials() -> None:
    from omk_system_manager.main import UpdateCredentialsRequest

    request = UpdateCredentialsRequest(id=VALID_ID, password=VALID_PASSWORD)
    assert VALID_ID not in repr(request)
    assert VALID_PASSWORD not in repr(request)


def test_restart_failure_reports_credentials_were_saved(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController(failure=True)
        response = client.put(
            "/api/broute/credentials",
            headers=headers(),
            json={"id": VALID_ID, "password": VALID_PASSWORD},
        )
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "credentials_saved_restart_failed",
        "credentials_saved": True,
    }
    assert (tmp_path / "credentials.yaml").exists()


def test_inactive_after_restart_reports_distinct_saved_state(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController(failure=True)
        client.app.state.controller.restart_and_verify = lambda: (_ for _ in ()).throw(
            ServiceControlError("credentials_saved_service_inactive")
        )
        response = client.put(
            "/api/broute/credentials",
            headers=headers(),
            json={"id": VALID_ID, "password": VALID_PASSWORD},
        )
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "credentials_saved_service_inactive",
        "credentials_saved": True,
    }


def test_missing_environment_token_prevents_application_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMK_SYSTEM_MANAGER_TOKEN", raising=False)
    app = create_app()
    with pytest.raises(RuntimeError, match="OMK_SYSTEM_MANAGER_TOKEN"):
        with TestClient(app):
            pass


def test_status_never_returns_password_or_raw_id(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController(active=False)
        client.put(
            "/api/broute/credentials",
            headers=headers(),
            json={"id": VALID_ID, "password": VALID_PASSWORD},
        )
        response = client.get("/api/broute/credentials/status", headers=headers())
    assert response.status_code == 200
    assert response.json()["service_active"] is False
    assert VALID_ID not in response.text
    assert VALID_PASSWORD not in response.text
