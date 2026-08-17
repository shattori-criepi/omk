"""HTTP entry point for the OMK touch display dashboard."""

import os
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import ParquetRepository
from app.data.display_repository import DisplayRepository
from app.data.settings_repository import DisplaySelection, SettingsError, SettingsRepository
from app.display_items import candidate_for, selected_items
from app.view_models import FreshnessStatus, worst_freshness
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


def get_display_repository() -> DisplayRepository:
    return DisplayRepository(Path(os.environ.get("OMK_LATEST_DATA_ROOT", "data/latest")))


def get_settings_repository() -> SettingsRepository:
    return SettingsRepository(Path(os.environ.get("OMK_DASHBOARD_SETTINGS_PATH", "data/dashboard/settings.json")))


@dataclass(frozen=True)
class StandardDashboard:
    items: list
    updated_at: str
    updated_at_iso: str
    freshness: FreshnessStatus

    def as_dict(self) -> dict:
        return {"mode": "standard", "items": [item.as_dict() for item in self.items], "updated_at": self.updated_at, "updated_at_iso": self.updated_at_iso, "freshness": self.freshness.value}


def _default_selections(candidates: list) -> list[DisplaySelection]:
    priority = {"net_power_w": 0, "load_power_w": 1, "power_w": 2, "temperature_c": 3, "temperature_celsius": 3, "relative_humidity_percent": 4, "co2_ppm": 5}
    ordered = sorted(candidates, key=lambda item: (priority.get(item.field, 100), item.id))
    selections: list[DisplaySelection] = []
    capacity = 0
    for index, item in enumerate(ordered):
        size = "large" if index == 0 else "small"
        cost = 3 if size == "large" else 1
        if capacity + cost > 6:
            break
        selections.append(DisplaySelection(item.id, size))
        capacity += cost
    return selections


def get_dashboard_view_model():
    """Build one consistent snapshot for both HTML and polling API responses."""
    display_repository = get_display_repository()
    candidates = [candidate_for(item) for item in display_repository.catalog()]
    selectable = [item for item in candidates if item.selectable]
    if selectable:
        settings = get_settings_repository().load_or_create(
            {item.id for item in candidates}, _default_selections(selectable)
        )
        now = datetime.now().astimezone()
        items = selected_items(display_repository, settings.items, now)
        statuses = [FreshnessStatus(item.freshness) for item in items] or [FreshnessStatus.UNAVAILABLE]
        updated = max((item.last_received_at for item in items if item.last_received_at), default="")
        return StandardDashboard(
            items=items, updated_at=updated.replace("T", " ") if updated else "--",
            updated_at_iso=updated, freshness=worst_freshness(*statuses),
        )
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


@app.get("/admin/display", response_class=HTMLResponse)
async def admin_display(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin_display.html", context={})


@app.get("/admin/sensors", response_class=HTMLResponse)
async def admin_sensors(request: Request) -> HTMLResponse:
    """Render the dedicated BLE setup screen, separate from the admin menu."""
    return templates.TemplateResponse(request=request, name="admin_sensors.html", context={})


@app.get("/admin/broute", response_class=HTMLResponse)
async def admin_broute(request: Request) -> HTMLResponse:
    """Render B-route configuration without embedding saved credentials or tokens."""
    return templates.TemplateResponse(request=request, name="admin_broute.html", context={})


@app.get("/admin/system", response_class=HTMLResponse)
async def admin_system(request: Request) -> HTMLResponse:
    """Render host power controls without exposing the system-manager token."""
    return templates.TemplateResponse(request=request, name="admin_system.html", context={})


async def _ble_request(method: str, path: str, body: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.request(method, f"{BLE_MANAGER_URL}{path}", json=body)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, _backend_error_detail(response, "BLE manager error"))
        return response.json()
    except httpx.RequestError as error:
        raise HTTPException(503, "BLE管理サービスに接続できません") from error


def _validation_detail(value: object) -> str | None:
    """Render FastAPI/Pydantic validation errors without exposing internals."""
    if isinstance(value, Mapping):
        message = value.get("msg")
        location = value.get("loc")
        if isinstance(message, str):
            if isinstance(location, (list, tuple)):
                field = ".".join(str(item) for item in location if item not in {"body", "query", "path"})
                return f"{field}: {message}" if field else message
            return message
        return None
    if isinstance(value, list):
        rendered = [detail for item in value if (detail := _validation_detail(item))]
        return "; ".join(rendered) if rendered else None
    return None


def _backend_error_detail(response: httpx.Response, fallback: str) -> str:
    """Preserve an upstream status while safely rendering its error body."""
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        # A traceback or path is not a browser-facing error message.
        if text and "Traceback" not in text and "/" not in text and "\\" not in text:
            return text[:300]
        return f"{fallback} (HTTP {response.status_code})"

    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    if isinstance(detail, str) and detail:
        return detail[:300]
    rendered = _validation_detail(detail)
    if rendered:
        return rendered[:300]
    return f"{fallback} (HTTP {response.status_code})"


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


@app.get("/api/admin/display-items")
async def display_items() -> dict:
    candidates = [candidate_for(item) for item in get_display_repository().catalog()]
    groups: dict[str, list[dict]] = {}
    for item in candidates:
        groups.setdefault(item.group, []).append(item.as_dict())
    return {"groups": [{"name": name, "items": sorted(items, key=lambda item: item["label"])} for name, items in sorted(groups.items())], "capacity": 6}


@app.get("/api/admin/dashboard-settings")
async def dashboard_settings() -> dict:
    candidates = [candidate_for(item) for item in get_display_repository().catalog()]
    settings = get_settings_repository().load_or_create({item.id for item in candidates}, _default_selections([item for item in candidates if item.selectable]))
    return {**settings.as_dict(), "capacity": 6}


@app.put("/api/admin/dashboard-settings")
async def update_dashboard_settings(request: Request) -> dict:
    candidates = [candidate_for(item) for item in get_display_repository().catalog()]
    try:
        settings = get_settings_repository().save_payload(await request.json(), {item.id for item in candidates if item.selectable})
    except SettingsError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, ValueError) as error:
        raise HTTPException(500, "表示設定を保存できません") from error
    return {**settings.as_dict(), "capacity": 6}

@app.get("/api/admin/nodes")
async def nodes() -> dict:
    return await _ble_request("GET", "/api/nodes")

@app.post("/api/admin/nodes/{node_id}/register", status_code=202)
async def register_node(node_id: str, request: Request) -> dict:
    return await _ble_request("POST", f"/api/nodes/{node_id}/register", await request.json())


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


@app.post("/api/admin/broute-retry")
async def retry_broute_connection() -> dict:
    return await _system_manager_request("POST", "/api/broute/retry")


@app.post("/api/admin/system/reboot")
async def reboot_system() -> dict:
    return await _system_manager_request("POST", "/api/system/reboot")


@app.post("/api/admin/system/shutdown")
async def shutdown_system() -> dict:
    return await _system_manager_request("POST", "/api/system/shutdown")


@app.get("/api/display")
async def display_api() -> dict:
    """Return the current dashboard snapshot for in-page refreshes."""
    return get_dashboard_view_model().as_dict()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
