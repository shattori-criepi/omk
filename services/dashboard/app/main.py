"""HTTP entry point for the OMK touch display dashboard."""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import ParquetRepository
from app.view_models import get_display_view_model

APP_DIR = Path(__file__).parent

app = FastAPI(title="OMK Dashboard")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
BLE_MANAGER_URL = os.environ.get("OMK_BLE_MANAGER_URL", "http://host.docker.internal:8787")
SYSTEM_MANAGER_URL = os.environ.get("OMK_SYSTEM_MANAGER_URL", "http://host.docker.internal:8788")
SYSTEM_MANAGER_TOKEN = os.environ.get("OMK_SYSTEM_MANAGER_TOKEN")


def get_parquet_repository() -> ParquetRepository:
    """Create the repository for daily Parquet totals."""
    return ParquetRepository(Path(os.environ.get("OMK_PROCESSED_DATA_ROOT", "data/processed")))


def get_latest_repository() -> LatestRepository:
    """Create the repository for collector-maintained latest records."""
    return LatestRepository(Path(os.environ.get("OMK_LATEST_DATA_ROOT", "data/latest")))


def get_dashboard_view_model():
    """Build one consistent snapshot for both HTML and polling API responses."""
    return get_display_view_model(get_latest_repository(), get_parquet_repository())


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    """Render the dashboard from latest JSON and daily Parquet totals."""
    return templates.TemplateResponse(
        request=request,
        name="display.html",
        context={"dashboard": get_dashboard_view_model()},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin.html", context={})


@app.get("/admin/sensors", response_class=HTMLResponse)
async def admin_sensors(request: Request) -> HTMLResponse:
    """Render the dedicated BLE setup screen, separate from the admin menu."""
    return templates.TemplateResponse(request=request, name="admin_sensors.html", context={})


@app.get("/admin/broute", response_class=HTMLResponse)
async def admin_broute(request: Request) -> HTMLResponse:
    """Render B-route configuration without embedding saved credentials or tokens."""
    return templates.TemplateResponse(request=request, name="admin_broute.html", context={})


async def _ble_request(method: str, path: str, body: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.request(method, f"{BLE_MANAGER_URL}{path}", json=body)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.json().get("detail", "BLE manager error"))
        return response.json()
    except httpx.RequestError as error:
        raise HTTPException(503, "BLE管理サービスに接続できません") from error


async def _system_manager_request(method: str, path: str, body: dict | None = None) -> dict:
    """Proxy management requests without exposing the host API token to browsers."""
    if not SYSTEM_MANAGER_TOKEN:
        raise HTTPException(503, "システム管理サービスの認証設定がありません")
    try:
        # system-manager can legitimately wait for its bounded 45-second
        # systemctl operation. Keep this longer than that bound so a working
        # host service is not mislabeled as unreachable by the proxy.
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                method,
                f"{SYSTEM_MANAGER_URL}{path}",
                json=body,
                headers={"Authorization": f"Bearer {SYSTEM_MANAGER_TOKEN}"},
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            detail = payload.get("detail", "システム管理サービスの処理に失敗しました") if isinstance(payload, dict) else "システム管理サービスの処理に失敗しました"
            raise HTTPException(response.status_code, detail)
        return payload
    except httpx.RequestError as error:
        raise HTTPException(503, "システム管理サービスに接続できません") from error


@app.get("/api/admin/sensors")
async def sensors() -> dict:
    return await _ble_request("GET", "/api/sensors")


@app.post("/api/admin/setup/scan")
async def start_ble_scan() -> dict:
    return await _ble_request("POST", "/api/setup/scan")


@app.delete("/api/admin/setup/scan")
async def stop_ble_scan() -> dict:
    return await _ble_request("DELETE", "/api/setup/scan")


@app.get("/api/admin/setup/candidates")
async def ble_candidates() -> dict:
    return await _ble_request("GET", "/api/setup/candidates")


@app.get("/api/admin/setup/suggested-sensor-id")
async def suggested_sensor_id(device_key: str) -> dict:
    return await _ble_request("GET", f"/api/setup/suggested-sensor-id?device_key={device_key}")


@app.post("/api/admin/sensors", status_code=201)
async def register_sensor(request: Request) -> dict:
    return await _ble_request("POST", "/api/sensors", await request.json())


@app.patch("/api/admin/sensors/{device_key}")
async def update_sensor(device_key: str, request: Request) -> dict:
    return await _ble_request("PATCH", f"/api/sensors/{device_key}", await request.json())


@app.delete("/api/admin/sensors/{device_key}")
async def delete_sensor(device_key: str) -> dict:
    return await _ble_request("DELETE", f"/api/sensors/{device_key}")


@app.get("/api/admin/broute-credentials")
async def broute_credentials_status() -> dict:
    return await _system_manager_request("GET", "/api/broute/credentials/status")


@app.put("/api/admin/broute-credentials")
async def update_broute_credentials(request: Request) -> dict:
    return await _system_manager_request("PUT", "/api/broute/credentials", await request.json())


@app.get("/api/display")
async def display_api() -> dict[str, str | bool]:
    """Return the current dashboard snapshot for in-page refreshes."""
    return get_dashboard_view_model().as_dict()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
