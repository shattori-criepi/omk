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


async def _ble_request(method: str, path: str, body: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.request(method, f"{BLE_MANAGER_URL}{path}", json=body)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.json().get("detail", "BLE manager error"))
        return response.json()
    except httpx.RequestError as error:
        raise HTTPException(503, "BLE管理サービスに接続できません") from error


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


@app.post("/api/admin/sensors", status_code=201)
async def register_sensor(request: Request) -> dict:
    return await _ble_request("POST", "/api/sensors", await request.json())


@app.get("/api/display")
async def display_api() -> dict[str, str | bool]:
    """Return the current dashboard snapshot for in-page refreshes."""
    return get_dashboard_view_model().as_dict()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
