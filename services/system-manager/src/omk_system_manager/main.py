"""Authenticated API for host-side B-route credential rotation."""

from __future__ import annotations

import hmac
import os
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .credentials import (
    CredentialValidationError,
    CredentialWriteError,
    credential_status,
    validate_broute_credentials,
    write_credentials_atomically,
)
from .service_control import BRouteServiceController, ServiceControlError
from .runtime_status import connection_status


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    credentials_path: Path
    systemctl_path: str
    status_path: Path = Path("/home/omkdev/projects/omk/data/broute-meter/status.json")

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
                    "/home/omkdev/projects/omk/broute-meter/config/credentials.yaml",
                )
            ),
            systemctl_path=os.environ.get("OMK_SYSTEMCTL_PATH", "/usr/bin/systemctl"),
            status_path=Path(
                os.environ.get(
                    "OMK_BROUTE_STATUS_PATH",
                    "/home/omkdev/projects/omk/data/broute-meter/status.json",
                )
            ),
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


def create_app(configured_settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = configured_settings or Settings.from_environment()
        app.state.settings = settings
        app.state.controller = BRouteServiceController(settings.systemctl_path)
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
        connection_state, status_updated_at, retry_after_seconds = connection_status(
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
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
