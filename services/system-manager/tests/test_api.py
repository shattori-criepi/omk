from __future__ import annotations

import logging
import json
import threading
import time
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


class PowerController:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.calls: list[str] = []

    def reboot(self) -> None:
        self._run("reboot")

    def shutdown(self) -> None:
        self._run("shutdown")

    def _run(self, operation: str) -> None:
        self.calls.append(operation)
        if self.failure is not None:
            raise ServiceControlError(self.failure)


def client_for(tmp_path: Path) -> TestClient:
    site_uuid = "123e4567-e89b-42d3-a456-426614174000"
    # This fixture models an already provisioned Gateway with matching metadata.
    (tmp_path / "site_uuid").write_text(site_uuid, encoding="utf-8")

    class MetadataStub:
        def get_site_uuid(self) -> str:
            return site_uuid

        def put_site_uuid(self, _: str) -> None:
            raise AssertionError("the matching SORACOM tag must not be updated")

    app = create_app(
        Settings(
            TOKEN,
            tmp_path / "credentials.yaml",
            "/usr/bin/systemctl",
            tmp_path / "status.json",
            tmp_path / "retry-request",
            tmp_path / "site_uuid",
        ),
        site_uuid_metadata=MetadataStub(),
    )
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_token_is_required_and_checked(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get("/api/broute/credentials/status").status_code == 401
        assert client.get("/api/broute/credentials/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_usb_export_requires_the_status_identity(tmp_path: Path) -> None:
    class UsbExportStub:
        def __init__(self) -> None:
            self.job = type("Job", (), {"public": lambda _: {"state": "idle"}})()
            self.started: list[tuple[str, str, list[str], str]] = []

        def status(self) -> dict[str, str]:
            return {"state": "available", "identity": "a" * 64}

        def start(self, from_date: str, to_date: str, datasets: list[str], identity: str) -> bool:
            if identity != "a" * 64:
                raise ValueError("usb_changed")
            self.started.append((from_date, to_date, datasets, identity))
            return True

    with client_for(tmp_path) as client:
        stub = UsbExportStub()
        client.app.state.usb_export = stub
        status = client.get("/api/export/usb/status", headers=headers())
        changed = client.post("/api/export/usb", headers=headers(), json={
            "from": "2026-08-01", "to": "2026-08-01", "datasets": ["sen66"], "expected_identity": "b" * 64,
        })
        accepted = client.post("/api/export/usb", headers=headers(), json={
            "from": "2026-08-01", "to": "2026-08-01", "datasets": ["sen66"], "expected_identity": "a" * 64,
        })
    assert status.json()["usb"]["identity"] == "a" * 64
    assert changed.status_code == 409 and changed.json()["detail"] == "usb_changed"
    assert accepted.status_code == 202 and stub.started[-1][-1] == "a" * 64


def test_access_point_apis_require_token_and_only_return_password_on_reveal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omk_system_manager.main as manager_main

    monkeypatch.setattr(manager_main, "read_access_point_status", lambda _: {"ssid": "OMK-TEST", "password_configured": True})
    monkeypatch.setattr(manager_main, "read_access_point_credentials", lambda _: {"ssid": "OMK-TEST", "password": "secret-canary"})
    with client_for(tmp_path) as client:
        assert client.get("/api/access-point/status").status_code == 401
        status = client.get("/api/access-point/status", headers=headers())
        credentials = client.get("/api/access-point/credentials", headers=headers())

    assert status.json() == {"ssid": "OMK-TEST", "password_configured": True}
    assert credentials.json() == {"ssid": "OMK-TEST", "password": "secret-canary"}


def test_usb_node_candidates_and_provision_are_token_protected_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import omk_system_manager.main as manager_main

    device = "/dev/ttyACM0"
    node_id = "9af9509eb8b6"
    monkeypatch.setattr(manager_main, "usb_candidates", lambda: [{"device": device, "node_id": node_id}])
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(manager_main, "provision_selected_node", lambda selected, identified: calls.append((selected, identified)))
    with client_for(tmp_path) as client:
        assert client.get("/api/nodes/usb-candidates").status_code == 401
        assert client.post("/api/nodes/usb-provision", json={"device": device, "node_id": node_id}).status_code == 401
        with caplog.at_level(logging.DEBUG):
            listed = client.get("/api/nodes/usb-candidates", headers=headers())
            provisioned = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": device, "node_id": node_id})
    assert listed.json() == {"nodes": [{"device": device, "node_id": node_id}], "busy": False}
    assert provisioned.json() == {"configured": True, "node_id": node_id}
    assert calls == [(device, node_id)]
    assert "synthetic-secret" not in provisioned.text + caplog.text


def test_usb_node_provision_rejects_unlisted_device_and_concurrent_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import omk_system_manager.main as manager_main
    from omk_system_manager.node_provisioning import ProvisioningError

    monkeypatch.setattr(manager_main, "provision_selected_node", lambda *_: (_ for _ in ()).throw(ProvisioningError("node_not_available")))
    with client_for(tmp_path) as client:
        invalid = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": "/tmp/not-a-device", "node_id": "9af9509eb8b6"})
        unavailable = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"})
        client.app.state.usb_node_provision_operation_lock.acquire()
        busy = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"})
        client.app.state.usb_node_provision_operation_lock.release()
    assert invalid.status_code == 400
    assert unavailable.status_code == 422 and "対象のUSB接続Node" in unavailable.json()["detail"]
    assert busy.status_code == 409 and "設定中" in busy.json()["detail"]


def test_usb_candidate_poll_returns_cached_result_without_serial_access_while_provisioning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import omk_system_manager.main as manager_main

    calls = []
    monkeypatch.setattr(manager_main, "usb_candidates", lambda: calls.append("serial") or [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}])
    with client_for(tmp_path) as client:
        first = client.get("/api/nodes/usb-candidates", headers=headers())
        client.app.state.usb_node_provision_operation_lock.acquire()
        busy = client.get("/api/nodes/usb-candidates", headers=headers())
        client.app.state.usb_node_provision_operation_lock.release()
    assert first.json() == {"nodes": [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}], "busy": False}
    assert busy.json() == {"nodes": [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}], "busy": True}
    assert calls == ["serial"]


def test_usb_provision_waits_for_candidate_scan_instead_of_returning_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import omk_system_manager.main as manager_main

    scanning = threading.Event()
    finish_scan = threading.Event()
    provisioned = threading.Event()

    def slow_candidates():
        scanning.set()
        assert finish_scan.wait(2)
        return [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}]

    monkeypatch.setattr(manager_main, "usb_candidates", slow_candidates)
    monkeypatch.setattr(manager_main, "provision_selected_node", lambda *_: provisioned.set())
    with client_for(tmp_path) as client:
        candidate_thread = threading.Thread(target=lambda: client.get("/api/nodes/usb-candidates", headers=headers()))
        candidate_thread.start()
        assert scanning.wait(2)
        response: dict[str, object] = {}
        provision_thread = threading.Thread(target=lambda: response.setdefault("value", client.post(
            "/api/nodes/usb-provision", headers=headers(), json={"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}
        )))
        provision_thread.start()
        deadline = time.monotonic() + 2
        while not client.app.state.usb_node_provision_operation_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.app.state.usb_node_provision_operation_lock.locked()
        finish_scan.set()
        candidate_thread.join(2)
        provision_thread.join(2)
    assert not candidate_thread.is_alive()
    assert not provision_thread.is_alive()
    assert response["value"].status_code == 200
    assert provisioned.is_set()


def test_usb_candidate_poll_does_not_open_serial_while_provision_operation_waits_or_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import omk_system_manager.main as manager_main

    serial_calls = []
    monkeypatch.setattr(manager_main, "usb_candidates", lambda: serial_calls.append("serial") or [])
    with client_for(tmp_path) as client:
        client.app.state.usb_node_candidates_cache = [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}]
        client.app.state.usb_node_provision_operation_lock.acquire()
        busy = client.get("/api/nodes/usb-candidates", headers=headers())
        client.app.state.usb_node_provision_operation_lock.release()
    assert busy.json() == {"nodes": [{"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"}], "busy": True}
    assert serial_calls == []


def test_usb_serial_and_operation_locks_are_released_after_provision_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import omk_system_manager.main as manager_main
    from omk_system_manager.node_provisioning import ProvisioningError

    monkeypatch.setattr(manager_main, "provision_selected_node", lambda *_: (_ for _ in ()).throw(ProvisioningError("node_not_available")))
    with client_for(tmp_path) as client:
        failed = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": "/dev/ttyACM0", "node_id": "9af9509eb8b6"})
        assert failed.status_code == 422
        assert client.app.state.usb_node_provision_operation_lock.acquire(blocking=False)
        client.app.state.usb_node_provision_operation_lock.release()
        assert client.app.state.usb_node_serial_access_lock.acquire(blocking=False)
        client.app.state.usb_node_serial_access_lock.release()


@pytest.mark.parametrize("path", ["/api/system/reboot", "/api/system/shutdown"])
def test_host_power_apis_require_token(tmp_path: Path, path: str) -> None:
    with client_for(tmp_path) as client:
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize(("path", "operation"), [("/api/system/reboot", "reboot"), ("/api/system/shutdown", "shutdown")])
def test_host_power_apis_invoke_only_the_fixed_operation(tmp_path: Path, path: str, operation: str) -> None:
    with client_for(tmp_path) as client:
        controller = PowerController()
        client.app.state.controller = controller
        response = client.post(path, headers=headers())

    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert controller.calls == [operation]


@pytest.mark.parametrize(("path", "code"), [("/api/system/reboot", "system_reboot_failed"), ("/api/system/shutdown", "system_shutdown_failed")])
def test_host_power_api_reports_systemctl_failure(tmp_path: Path, path: str, code: str) -> None:
    with client_for(tmp_path) as client:
        client.app.state.controller = PowerController(code)
        response = client.post(path, headers=headers())

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": code}}


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
        "connection_state": "starting",
        "status_updated_at": None,
        "retry_after_seconds": None,
        "connection_attempt": None,
        "connection_state_source": "credentials_update_restart",
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


def test_application_starts_with_generated_local_uuid_when_soracom_is_unavailable(tmp_path: Path) -> None:
    import asyncio
    from omk_system_manager.site_uuid import MetadataServiceError, read_local_site_uuid

    class UnavailableMetadata:
        def get_site_uuid(self) -> None:
            raise MetadataServiceError("unavailable")

        def put_site_uuid(self, _: str) -> None:
            raise AssertionError("sync must not be attempted when metadata cannot be read")

    site_uuid_path = tmp_path / "site_uuid"
    app = create_app(
        Settings(TOKEN, tmp_path / "credentials.yaml", "/usr/bin/systemctl", site_uuid_path=site_uuid_path),
        site_uuid_metadata=UnavailableMetadata(),
    )
    async def start_and_check() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.site_uuid == read_local_site_uuid(site_uuid_path)

    asyncio.run(start_and_check())


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


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("adapter_missing", "adapter_missing"),
        ("adapter_initializing", "adapter_initializing"),
        ("scan_error", "scan_error"),
        ("scanning", "scanning"),
        ("authenticating", "authenticating"),
        ("connected", "connected"),
        ("retry_wait", "retry_wait"),
        ("authentication_error", "authentication_error"),
        ("connection_error", "connection_error"),
    ],
)
def test_status_returns_broute_runtime_state_without_secrets(
    tmp_path: Path,
    state: str,
    expected: str,
) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps({"state": state, "updated_at": "2026-08-14T00:00:00+00:00"}),
        encoding="utf-8",
    )
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        response = client.get("/api/broute/credentials/status", headers=headers())

    assert response.status_code == 200
    assert response.json()["connection_state"] == expected
    assert response.json()["status_updated_at"] == "2026-08-14T00:00:00+00:00"
    assert response.json()["retry_after_seconds"] is None
    assert response.json()["connection_state_source"] == "runtime_status"
    assert VALID_ID not in response.text
    assert VALID_PASSWORD not in response.text


def test_inactive_service_is_stopped_even_with_a_stale_connected_status(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        '{"state":"connected","updated_at":"2026-08-14T00:00:00+00:00"}',
        encoding="utf-8",
    )
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController(active=False)
        response = client.get("/api/broute/credentials/status", headers=headers())

    assert response.json()["service_active"] is False
    assert response.json()["connection_state"] == "stopped"


def test_active_service_with_unreadable_status_is_not_reported_as_starting(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text("{", encoding="utf-8")
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        response = client.get("/api/broute/credentials/status", headers=headers())

    assert response.json()["connection_state"] == "status_unavailable"
    assert response.json()["status_updated_at"] is None


def test_status_returns_retry_wait_delay_without_credentials(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        '{"state":"retry_wait","updated_at":"2026-08-14T00:00:00+00:00","retry_after_seconds":30}',
        encoding="utf-8",
    )
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        response = client.get("/api/broute/credentials/status", headers=headers())

    assert response.json()["connection_state"] == "retry_wait"
    assert response.json()["retry_after_seconds"] == 30
    assert VALID_ID not in response.text
    assert VALID_PASSWORD not in response.text


def test_retry_request_is_allowed_only_after_repeated_retry_wait(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        '{"state":"retry_wait","updated_at":"2026-08-14T00:00:00+00:00","retry_after_seconds":300,"connection_attempt":3}',
        encoding="utf-8",
    )
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        response = client.post("/api/broute/retry", headers=headers())

    assert response.json() == {"accepted": True}
    assert (tmp_path / "retry-request").read_text(encoding="ascii") == "retry\n"
    assert (tmp_path / "retry-request").stat().st_mode & 0o777 == 0o600


def test_retry_request_is_rejected_before_extended_retry_wait(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        '{"state":"retry_wait","updated_at":"2026-08-14T00:00:00+00:00","retry_after_seconds":30,"connection_attempt":2}',
        encoding="utf-8",
    )
    with client_for(tmp_path) as client:
        client.app.state.controller = ActiveController()
        response = client.post("/api/broute/retry", headers=headers())

    assert response.status_code == 409
    assert not (tmp_path / "retry-request").exists()


def test_usb_duplicate_identity_clears_cache_and_releases_scan_lock(tmp_path, monkeypatch):
    import omk_system_manager.main as manager_main
    from omk_system_manager.node_provisioning import ProvisioningError
    def ambiguous(): raise ProvisioningError("ambiguous_node_identity")
    monkeypatch.setattr(manager_main, "usb_candidates", ambiguous)
    with client_for(tmp_path) as client:
        client.app.state.usb_node_candidates_cache = [{"device": "/dev/ttyACM0", "node_id": "020000000001"}]
        response = client.get("/api/nodes/usb-candidates", headers=headers())
        assert response.status_code == 422
        assert client.app.state.usb_node_candidates_cache == []
        assert client.app.state.usb_node_serial_access_lock.acquire(blocking=False)
        client.app.state.usb_node_serial_access_lock.release()


def test_dashboard_selected_identity_reaches_final_guard_before_send(tmp_path, monkeypatch):
    from omk_system_manager import node_provisioning as core
    selected = "020000000001"
    device = "/dev/ttyACM0"
    monkeypatch.setattr(core, "candidate_devices", lambda: [device])
    monkeypatch.setattr(core, "usb_candidates", lambda: [{"device": device, "node_id": selected}])
    monkeypatch.setattr(core, "read_gateway_wifi", lambda: ("OMK-TEST", "synthetic-secret"))
    sent, saved = [], []
    class Replacement:
        def __init__(self, path): assert path == device
        def request(self, request, timeout, matches):
            sent.append(request)
            if request["command"] == "set_wifi": saved.append(request)
            return {"protocol_version": 2, "status": "ok", "node_id": "020000000002"}
        def close(self): pass
    monkeypatch.setattr(core, "SerialJson", Replacement)
    with client_for(tmp_path) as client:
        response = client.post("/api/nodes/usb-provision", headers=headers(), json={"device": device, "node_id": selected})
    assert response.status_code == 422
    assert saved == [] and sent == [{"command": "identify", "protocol_version": 2}]
    assert "synthetic-secret" not in response.text


def test_usb_setup_job_auth_conflicts_and_polling(tmp_path, monkeypatch):
    from omk_system_manager import node_setup, main
    entered = threading.Event()
    release = threading.Event()

    def work(device, node_id, confirmed, stage):
        stage('flashing_firmware')
        entered.set()
        assert release.wait(3)
        raise RuntimeError('secret-psk 02:00:00:00:00:01')

    monkeypatch.setattr(node_setup, 'setup', work)
    monkeypatch.setattr(main, 'usb_candidates', lambda: pytest.fail('Polling opened serial'))
    body = dict(device='/dev/ttyACM0', node_id='000000000001')
    with client_for(tmp_path) as client:
        assert client.post('/api/nodes/usb-setup', json=body).status_code == 401
        assert client.get('/api/nodes/usb-setup/status').status_code == 401
        for extra in ({'offset': '0xf000'}, {'confirm_atom_s3_lite': 'true'}, {'device': '/dev/ttyUSB0'}):
            assert client.post('/api/nodes/usb-setup', headers=headers(), json={**body, **extra}).status_code == 400
        assert client.post('/api/nodes/usb-setup', headers=headers(), json=body).status_code == 202
        assert entered.wait(3)
        try:
            assert client.post('/api/nodes/usb-setup', headers=headers(), json=body).status_code == 409
            assert client.post('/api/nodes/usb-provision', headers=headers(), json=body).status_code == 409
            assert client.get('/api/nodes/usb-candidates', headers=headers()).json()['busy'] is True
            assert client.get('/api/nodes/usb-setup/status', headers=headers()).json()['stage'] == 'flashing_firmware'
        finally:
            release.set()
            client.app.state.usb_node_setup.worker.join(3)
        status = client.get('/api/nodes/usb-setup/status', headers=headers())
        assert status.json() == dict(stage='failed', node_id=body['node_id'], error='setup_failed')
        assert 'secret-psk' not in status.text and '02:00' not in status.text
        assert not client.app.state.usb_node_serial_access_lock.locked()
        assert not client.app.state.usb_node_provision_operation_lock.locked()
