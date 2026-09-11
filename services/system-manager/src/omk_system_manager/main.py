"""Authenticated API for host-side B-route credential rotation."""

from __future__ import annotations

import hmac
import os
import subprocess
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .credentials import (
    CredentialValidationError,
    CredentialWriteError,
    credential_status,
    validate_broute_credentials,
    write_credentials_atomically,
)
from .access_point import AccessPointCredentialError, read_access_point_credentials, read_access_point_status
from .service_control import BRouteServiceController, ServiceControlError
from .runtime_status import connection_status, request_immediate_retry
from .site_uuid import SoracomMetadataClient, resolve_site_uuid
from .usb_export import DATASETS, UsbExportController
from .node_provisioning import ProvisioningError, provision_selected_node
from .node_setup import SetupController, setup_candidates as usb_candidates


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BROUTE_CREDENTIALS_PATH = REPOSITORY_ROOT / "services/broute-meter/config/credentials.yaml"
DEFAULT_BROUTE_STATUS_PATH = REPOSITORY_ROOT / "data/broute-meter/status.json"
DEFAULT_BROUTE_RETRY_REQUEST_PATH = REPOSITORY_ROOT / "data/broute-meter/retry-request"
DEFAULT_SITE_UUID_PATH = REPOSITORY_ROOT / "data/site/site_uuid"


class SiteUUIDMetadataClient(Protocol):
    def get_site_uuid(self) -> str | None: ...

    def put_site_uuid(self, value: str) -> None: ...


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    credentials_path: Path
    systemctl_path: str
    status_path: Path = DEFAULT_BROUTE_STATUS_PATH
    retry_request_path: Path = DEFAULT_BROUTE_RETRY_REQUEST_PATH
    site_uuid_path: Path = DEFAULT_SITE_UUID_PATH
    access_point_profile: str = "omk-ap"
    usb_export_command: str = "/usr/local/bin/omk-export-usb"

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.environ.get("OMK_SYSTEM_MANAGER_TOKEN")
        if not token:
            raise RuntimeError("OMK_SYSTEM_MANAGER_TOKEN must be configured")
        return cls(
            token=token,
            credentials_path=Path(
                os.environ.get(
                    "OMK_BROUTE_CREDENTIALS_PATH",
                    str(DEFAULT_BROUTE_CREDENTIALS_PATH),
                )
            ),
            systemctl_path=os.environ.get("OMK_SYSTEMCTL_PATH", "/usr/bin/systemctl"),
            status_path=Path(
                os.environ.get(
                    "OMK_BROUTE_STATUS_PATH",
                    str(DEFAULT_BROUTE_STATUS_PATH),
                )
            ),
            retry_request_path=Path(
                os.environ.get(
                    "OMK_BROUTE_RETRY_REQUEST_PATH",
                    str(DEFAULT_BROUTE_RETRY_REQUEST_PATH),
                )
            ),
            site_uuid_path=Path(
                os.environ.get(
                    "OMK_SITE_UUID_PATH",
                    str(DEFAULT_SITE_UUID_PATH),
                )
            ),
            access_point_profile=os.environ.get("OMK_AP_CONNECTION_NAME", "omk-ap"),
            usb_export_command=os.environ.get("OMK_EXPORT_USB_COMMAND", "/usr/local/bin/omk-export-usb"),
        )


class UpdateCredentialsRequest(BaseModel):
    # Validation is deliberately performed after parsing, so error responses
    # never include a rejected ID or password.
    model_config = ConfigDict(extra="forbid")
    id: object
    password: object

    def __repr_args__(self):
        """Do not disclose credentials if a model is accidentally repr'd."""
        return []

class UsbExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    datasets: list[str]
    expected_identity: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def validate_request(cls, body: "UsbExportRequest") -> None:
        from datetime import date
        try: start, end = date.fromisoformat(body.from_), date.fromisoformat(body.to)
        except ValueError: raise ValueError("invalid_date")
        if start > end: raise ValueError("invalid_date_range")
        if not body.datasets or any(dataset not in DATASETS for dataset in body.datasets): raise ValueError("invalid_datasets")


class UsbNodeProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str = Field(pattern=r"^/dev/(serial/by-id/[^/]+|ttyACM[0-9]+)$")
    node_id: str = Field(pattern=r"^[0-9a-f]{12}$")


class UsbNodeSetupRequest(UsbNodeProvisionRequest):
    confirm_atom_s3_lite: bool = Field(default=False, strict=True)


USB_NODE_MESSAGES = {
    "node_not_available": "対象のUSB接続Nodeを確認できません。接続を確認して再読み込みしてください。",
    "gateway_credential_unavailable": "GatewayのWi-Fi設定を取得できませんでした。",
    "set_wifi_failed": "NodeへWi-Fi設定を送信できませんでした。",
    "unsupported_usb_protocol": "Nodeのfirmwareを更新してからUSB設定をやり直してください。",
    "ambiguous_node_identity": "複数のUSB機器が同じNode IDを返しました。接続を確認してください。",
    "node_identity_changed": "対象Nodeの確認に失敗しました。再読み込みしてやり直してください。",
    "mqtt_registration_timeout": "Nodeの再起動後のMQTT登録を確認できませんでした。",
}
USB_NODE_SERIAL_ACCESS_TIMEOUT_SECONDS = 5.0


def scan_usb_candidates_with_serial_lock(
    serial_lock: threading.Lock,
    scan: Callable[[], list[dict[str, str | bool]]],
) -> list[dict[str, str | bool]]:
    """Run an already-authorized candidate scan and always release its lock."""
    try:
        return scan()
    finally:
        serial_lock.release()


def create_app(
    configured_settings: Settings | None = None,
    *,
    site_uuid_metadata: SiteUUIDMetadataClient | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = configured_settings or Settings.from_environment()
        app.state.settings = settings
        app.state.controller = BRouteServiceController(settings.systemctl_path)
        app.state.usb_export = UsbExportController(settings.usb_export_command)
        # An operation lock distinguishes a second Provisioning request from
        # an in-flight read-only candidate scan.  The latter only owns the
        # serial lock and must not cause a misleading 409 response.
        app.state.usb_node_provision_operation_lock = threading.Lock()
        app.state.usb_node_serial_access_lock = threading.Lock()
        app.state.usb_node_candidates_cache = []
        app.state.usb_node_setup = SetupController(app.state.usb_node_provision_operation_lock, app.state.usb_node_serial_access_lock)
        app.state.site_uuid = resolve_site_uuid(
            settings.site_uuid_path,
            site_uuid_metadata or SoracomMetadataClient(),
        )
        yield

    app = FastAPI(title="OMK System Manager", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": "リクエスト形式が不正です"})

    def authenticated(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        settings: Settings = request.app.state.settings
        expected = f"Bearer {settings.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=401,
                detail="認証に失敗しました",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/api/broute/credentials/status", dependencies=[Depends(authenticated)])
    def broute_credentials_status(request: Request) -> dict[str, bool | str | float | None]:
        settings: Settings = request.app.state.settings
        configured, identifier_masked, password_configured = credential_status(settings.credentials_path)
        controller: BRouteServiceController = request.app.state.controller
        service_active = controller.is_active()
        connection_state, status_updated_at, retry_after_seconds, connection_attempt, connection_state_source = connection_status(
            settings.status_path,
            service_active=service_active,
        )
        return {
            "configured": configured,
            "id_masked": identifier_masked,
            "password_configured": password_configured,
            "service_active": service_active,
            "connection_state": connection_state,
            "status_updated_at": status_updated_at,
            "retry_after_seconds": retry_after_seconds,
            "connection_attempt": connection_attempt,
            "connection_state_source": connection_state_source,
        }

    @app.put("/api/broute/credentials", dependencies=[Depends(authenticated)])
    def update_broute_credentials(request: Request, body: UpdateCredentialsRequest) -> dict[str, bool | str | float | None]:
        try:
            identifier, password = validate_broute_credentials(body.id, body.password)
        except CredentialValidationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

        settings: Settings = request.app.state.settings
        try:
            write_credentials_atomically(settings.credentials_path, identifier, password)
        except CredentialWriteError as error:
            raise HTTPException(status_code=500, detail="認証情報を保存できませんでした") from error

        controller: BRouteServiceController = request.app.state.controller
        try:
            controller.restart_and_verify()
        except ServiceControlError as error:
            # The file was already atomically replaced; make that state explicit
            # while never returning subprocess stdout/stderr.
            raise HTTPException(
                status_code=502,
                detail={"code": error.code, "credentials_saved": True},
            ) from None
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(
                status_code=502,
                detail={"code": "credentials_saved_restart_failed", "credentials_saved": True},
            ) from None

        configured, identifier_masked, password_configured = credential_status(settings.credentials_path)
        return {
            "configured": configured,
            "id_masked": identifier_masked,
            "password_configured": password_configured,
            "service_active": True,
            # A restart makes any earlier connected record stale. The new
            # process writes its authoritative state before attempting PANA.
            "connection_state": "starting",
            "status_updated_at": None,
            "retry_after_seconds": None,
            "connection_attempt": None,
            "connection_state_source": "credentials_update_restart",
        }

    @app.post("/api/broute/retry", dependencies=[Depends(authenticated)])
    def retry_broute_connection(request: Request) -> dict[str, bool]:
        settings: Settings = request.app.state.settings
        controller: BRouteServiceController = request.app.state.controller
        service_active = controller.is_active()
        state, _, _, connection_attempt, _ = connection_status(
            settings.status_path,
            service_active=service_active,
        )
        if state != "retry_wait" or connection_attempt is None or connection_attempt <= 2:
            raise HTTPException(status_code=409, detail="現在は即時再試行できません")
        try:
            request_immediate_retry(settings.retry_request_path)
        except OSError as error:
            raise HTTPException(status_code=500, detail="再試行要求を保存できませんでした") from error
        return {"accepted": True}

    @app.post("/api/system/reboot", dependencies=[Depends(authenticated)])
    def reboot_system(request: Request) -> dict[str, bool]:
        controller: BRouteServiceController = request.app.state.controller
        try:
            controller.reboot()
        except ServiceControlError as error:
            raise HTTPException(status_code=502, detail={"code": error.code}) from None
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(status_code=502, detail={"code": "system_reboot_failed"}) from None
        return {"accepted": True}

    @app.post("/api/system/shutdown", dependencies=[Depends(authenticated)])
    def shutdown_system(request: Request) -> dict[str, bool]:
        controller: BRouteServiceController = request.app.state.controller
        try:
            controller.shutdown()
        except ServiceControlError as error:
            raise HTTPException(status_code=502, detail={"code": error.code}) from None
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(status_code=502, detail={"code": "system_shutdown_failed"}) from None
        return {"accepted": True}

    @app.get("/api/export/usb/status", dependencies=[Depends(authenticated)])
    def usb_export_status(request: Request) -> dict:
        controller: UsbExportController = request.app.state.usb_export
        return {"usb": controller.status(), "export": controller.job.public()}

    @app.post("/api/export/usb", dependencies=[Depends(authenticated)], status_code=202)
    def start_usb_export(request: Request, body: UsbExportRequest) -> dict[str, bool]:
        try:
            UsbExportRequest.validate_request(body)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from None
        controller: UsbExportController = request.app.state.usb_export
        try:
            accepted = controller.start(body.from_, body.to, body.datasets, body.expected_identity)
        except ValueError:
            raise HTTPException(409, detail="usb_changed") from None
        if not accepted:
            raise HTTPException(409, detail="export_running")
        return {"accepted": True}

    @app.get("/api/access-point/status", dependencies=[Depends(authenticated)])
    def access_point_status(request: Request) -> dict[str, str | bool]:
        settings: Settings = request.app.state.settings
        try:
            return read_access_point_status(settings.access_point_profile)
        except AccessPointCredentialError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

    @app.get("/api/nodes/usb-candidates", dependencies=[Depends(authenticated)])
    def list_usb_node_candidates(request: Request) -> dict:
        operation_lock: threading.Lock = request.app.state.usb_node_provision_operation_lock
        serial_lock: threading.Lock = request.app.state.usb_node_serial_access_lock
        # While a Provisioning operation owns its operation lock, candidate
        # polling is cache-only and never opens the same serial device.
        if operation_lock.locked() or not serial_lock.acquire(blocking=False):
            return {"nodes": request.app.state.usb_node_candidates_cache, "busy": True}
        try:
            candidates = scan_usb_candidates_with_serial_lock(serial_lock, usb_candidates)
        except ProvisioningError as error:
            request.app.state.usb_node_candidates_cache = []
            raise HTTPException(status_code=422, detail=USB_NODE_MESSAGES.get(error.code, "USB接続Nodeを確認できませんでした。")) from None
        request.app.state.usb_node_candidates_cache = candidates
        return {"nodes": candidates, "busy": False}

    @app.post("/api/nodes/usb-setup", dependencies=[Depends(authenticated)], status_code=202)
    def setup_usb_node(request: Request, body: UsbNodeSetupRequest) -> dict:
        if not request.app.state.usb_node_setup.start(body.device, body.node_id, body.confirm_atom_s3_lite):
            raise HTTPException(409, detail="別のNodeを設定中です")
        return {"accepted": True, "node_id": body.node_id}

    @app.get("/api/nodes/usb-setup/status", dependencies=[Depends(authenticated)])
    def setup_usb_node_status(request: Request) -> dict:
        return request.app.state.usb_node_setup.status()

    @app.post("/api/nodes/usb-provision", dependencies=[Depends(authenticated)])
    def provision_usb_node(request: Request, body: UsbNodeProvisionRequest) -> dict[str, str | bool]:
        operation_lock: threading.Lock = request.app.state.usb_node_provision_operation_lock
        serial_lock: threading.Lock = request.app.state.usb_node_serial_access_lock
        if not operation_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="別のNodeを設定中です")
        try:
            # A scan already in progress is allowed to finish.  It is bounded
            # and no longer probes unrelated ttyUSB hardware, so this should
            # normally take only a short time.
            if not serial_lock.acquire(timeout=USB_NODE_SERIAL_ACCESS_TIMEOUT_SECONDS):
                raise HTTPException(status_code=503, detail="USB接続Nodeの確認待ちがタイムアウトしました。もう一度お試しください。")
            try:
                provision_selected_node(body.device, body.node_id)
            finally:
                serial_lock.release()
        except ProvisioningError as error:
            raise HTTPException(status_code=422, detail=USB_NODE_MESSAGES.get(error.code, "Nodeを設定できませんでした。")) from None
        finally:
            operation_lock.release()
        return {"configured": True, "node_id": body.node_id}

    @app.get("/api/access-point/credentials", dependencies=[Depends(authenticated)])
    def access_point_credentials(request: Request) -> dict[str, str]:
        """Return the PSK only to the authenticated Dashboard backend."""
        settings: Settings = request.app.state.settings
        try:
            return read_access_point_credentials(settings.access_point_profile)
        except AccessPointCredentialError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
