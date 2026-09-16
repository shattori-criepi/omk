"""HTTP entry point for the OMK touch display dashboard."""

import os
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import httpx
import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import EnergyTotals, ParquetRepository
from app.data.display_repository import DisplayRepository
from app.data.settings_repository import DashboardSettings, DisplayBlock, SettingsError, SettingsRepository, default_layout_pattern, item_limit
from app.display_items import DisplayItem, catalog_items_with_latest, display_candidates, display_item_migrations, selected_blocks
from app.recommendations import BROUTE_GROUP, clock_item_ids, recommended_blocks
from app.view_models import FreshnessStatus, format_timestamp_seconds, worst_freshness
from app.view_models import get_display_view_model
from app.demo import demo_candidates, demo_custom_preset

APP_DIR = Path(__file__).parent
_DERIVED_ENERGY_CACHE: tuple[datetime, list[DisplayItem]] | None = None
JST = ZoneInfo("Asia/Tokyo")

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
class BlockDashboard:
    mode: str
    blocks: list
    updated_at: str
    updated_at_iso: str
    freshness: FreshnessStatus

    def as_dict(self) -> dict:
        return {"mode": self.mode, "blocks": [block.as_dict() for block in self.blocks], "updated_at": self.updated_at, "updated_at_iso": self.updated_at_iso, "freshness": self.freshness.value}


@dataclass(frozen=True)
class ClockDashboard:
    date: str
    time: str
    supplemental: list[DisplayItem]
    updated_at: str
    updated_at_iso: str
    freshness: FreshnessStatus

    @property
    def mode(self) -> str:
        return "clock"

    def as_dict(self) -> dict:
        return {
            "mode": self.mode, "date": self.date, "time": self.time,
            "supplemental": [item.as_dict() for item in self.supplemental],
            "updated_at": self.updated_at, "updated_at_iso": self.updated_at_iso,
            "freshness": self.freshness.value,
        }


def _default_blocks(candidates: list) -> list[DisplayBlock]:
    priority = {"net_power_w": 0, "load_power_w": 1, "power_w": 2, "temperature_c": 3, "temperature_celsius": 3, "relative_humidity_percent": 4, "co2_ppm": 5}
    grouped: dict[str, list] = {}
    for item in sorted(candidates, key=lambda item: (priority.get(item.field, 100), item.id)):
        grouped.setdefault(item.group, []).append(item)
    ordered_groups = sorted(grouped.values(), key=lambda items: (priority.get(items[0].field, 100), items[0].group))
    blocks: list[DisplayBlock] = []
    capacity = 0
    for index, items in enumerate(ordered_groups):
        item = items[0]
        size = "large" if index == 0 else "small"
        cost = 3 if size == "large" else 1
        if capacity + cost > 6:
            break
        layout_pattern = default_layout_pattern(size)
        blocks.append(DisplayBlock(
            block_id=f"block_{index + 1}", group=item.group, title=_default_block_title(item.group), size=size,
            primary_item_id=item.id, item_ids=tuple(candidate.id for candidate in items[:item_limit(size, layout_pattern)]),
            layout_pattern=layout_pattern,
        ))
        capacity += cost
    return blocks


def _default_block_title(group: str) -> str:
    return "パワコン" if group == "パワコン" else group


def _dashboard_candidates(now: datetime | None = None) -> list[DisplayItem]:
    """Reuse the legacy JST daily-total repository for derived Block values."""
    now = (now or datetime.now(JST)).astimezone(JST)
    global _DERIVED_ENERGY_CACHE
    candidates = display_candidates(get_display_repository(), now)
    if _DERIVED_ENERGY_CACHE is not None and (now - _DERIVED_ENERGY_CACHE[0]).total_seconds() < 60:
        return candidates + _DERIVED_ENERGY_CACHE[1]
    try:
        totals = get_parquet_repository().today_energy_totals(now.date())
        derived = [
            DisplayItem(item_id, label, "パワコン", "derived:broute_interval_energy", "broute-derived", metric, "number", "kWh", "電力", role, True, now.isoformat() if value is not None else "", f"{value:.1f}" if value is not None else "--", "normal" if value is not None else "unavailable", short_label=short)
            for item_id, label, short, metric, role, value in (("derived:energy:today_import_kwh", "パワコン 本日の買電量", "本日の買電量", "today_import_kwh", "today_import_energy", totals.import_energy_kwh), ("derived:energy:today_export_kwh", "パワコン 本日の売電量", "本日の売電量", "today_export_kwh", "today_export_energy", totals.export_energy_kwh))
        ]
    except (OSError, ValueError):
        derived = [DisplayItem(item_id, label, "パワコン", "derived:broute_interval_energy", "broute-derived", item_id.rsplit(":", 1)[-1], "number", "kWh", "電力", role, True, "", "--", "unavailable", short_label=short) for item_id, label, short, role in (("derived:energy:today_import_kwh", "パワコン 本日の買電量", "本日の買電量", "today_import_energy"), ("derived:energy:today_export_kwh", "パワコン 本日の売電量", "本日の売電量", "today_export_energy"))]
    _DERIVED_ENERGY_CACHE = (now, derived)
    return candidates + derived


def _settings_for(candidates: list[DisplayItem]):
    selectable = [item for item in candidates if item.selectable]
    return get_settings_repository().load_or_create(
        _available_display_groups(candidates), _default_blocks(selectable),
        display_item_migrations(candidates), recommended_blocks(selectable),
    )


def _available_display_groups(candidates: list[DisplayItem]) -> dict[str, str | frozenset[str]]:
    """Allow the B-route daily total in its compatible custom/recommended Blocks."""
    groups: dict[str, str | frozenset[str]] = {item.id: item.group for item in candidates}
    for item in candidates:
        if item.semantic_role in {"today_import_energy", "today_export_energy"}:
            groups[item.id] = frozenset({item.group, BROUTE_GROUP})
    return groups


def get_dashboard_view_model(mode_override: str | None = None, *, outdoor_device_ids: frozenset[str] = frozenset()):
    """Build one consistent snapshot for both HTML and polling API responses."""
    display_repository = get_display_repository()
    candidates = _dashboard_candidates()
    selectable = [item for item in candidates if item.selectable]
    if selectable or mode_override is not None:
        settings = _settings_for(candidates)
        now = datetime.now(JST)
        current_candidates = _dashboard_candidates(now)
        mode = mode_override or settings.mode
        active_blocks = settings.recommended_blocks if mode == "recommended" else settings.custom_blocks
        if settings.demo_enabled:
            # Settings and admin candidates must always come from real sources.
            # This overlay exists only for this HTML/API response.
            if mode == "custom":
                current_candidates, active_blocks = demo_custom_preset(current_candidates, outdoor_device_ids)
            else:
                current_candidates = demo_candidates(current_candidates)
        if mode == "clock":
            item_ids = clock_item_ids(current_candidates) if settings.demo_enabled else settings.clock_item_ids
            supplemental = _clock_supplemental(item_ids, current_candidates)
            return _clock_dashboard(now, supplemental)
        if settings.demo_enabled and mode == "recommended":
            active_blocks = tuple(recommended_blocks(current_candidates))
        blocks = selected_blocks(display_repository, active_blocks, now, current_candidates)
        statuses = [FreshnessStatus(block.freshness) for block in blocks] or [FreshnessStatus.UNAVAILABLE]
        updated = max((block.last_received_at for block in blocks if block.last_received_at), default="")
        return BlockDashboard(mode=mode,
            blocks=blocks, updated_at=format_timestamp_seconds(updated) if updated else "--",
            updated_at_iso=updated, freshness=worst_freshness(*statuses),
        )
    # A persisted clock configuration remains useful even when no source is
    # presently discoverable: the clock itself must never depend on a sensor.
    repository = get_settings_repository()
    if repository.path.exists():
        settings = _settings_for(candidates)
        if settings.mode == "clock":
            now = datetime.now(JST)
            return _clock_dashboard(now, [])
    return get_display_view_model(get_latest_repository(), get_parquet_repository())


def _clock_date(now: datetime) -> str:
    weekdays = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
    return f"{now.year}年{now.month}月{now.day}日 {weekdays[now.weekday()]}"


def _clock_dashboard(now: datetime, supplemental: list[DisplayItem]) -> ClockDashboard:
    statuses = [FreshnessStatus(item.freshness) for item in supplemental] or [FreshnessStatus.UNAVAILABLE]
    updated = max((item.last_received_at for item in supplemental if item.last_received_at), default="")
    return ClockDashboard(
        _clock_date(now), now.strftime("%H:%M"), supplemental,
        format_timestamp_seconds(updated) if updated else "--", updated,
        worst_freshness(*statuses),
    )


def _clock_supplemental(item_ids: tuple[str, ...], candidates: list[DisplayItem]) -> list[DisplayItem]:
    """Keep saved clock slots stable while applying their fixed visual order."""
    current = {item.id: item for item in candidates}
    priority = {"grid_power": 0, "temperature": 1, "humidity": 2, "co2": 3}
    selected = [(index, current[item_id]) for index, item_id in enumerate(item_ids) if item_id in current]
    return [item for _index, item in sorted(selected, key=lambda entry: (priority.get(entry[1].semantic_role, 99), entry[0]))]


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send browser users from the Gateway URL to the display."""
    return RedirectResponse(url="/display", status_code=307)


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    """Render the dashboard from latest JSON and daily Parquet totals."""
    requested_demo_mode = request.query_params.get("demo_mode")
    if requested_demo_mode not in {None, "custom", "clock", "recommended"}:
        raise HTTPException(400, "表示モードが正しくありません")
    demo_enabled = _demo_enabled()
    outdoor_device_ids = await _demo_outdoor_device_ids() if demo_enabled and requested_demo_mode in {None, "custom"} else frozenset()
    return templates.TemplateResponse(
        request=request,
        name="display.html",
        context={"dashboard": get_dashboard_view_model(requested_demo_mode or "custom", outdoor_device_ids=outdoor_device_ids) if demo_enabled else get_dashboard_view_model(), "demo_enabled": demo_enabled},
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


@app.get("/admin/access-point", response_class=HTMLResponse)
async def admin_access_point(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin_access_point.html", context={})

@app.get("/admin/export", response_class=HTMLResponse)
async def admin_export(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin_export.html", context={})


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
        async with httpx.AsyncClient(timeout=90) as client:
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
    candidates = _dashboard_candidates(datetime.now().astimezone())
    groups: dict[str, list[dict]] = {}
    for item in candidates:
        groups.setdefault(item.group, []).append(item.as_dict())
    return {"groups": [{"name": name, "items": sorted(items, key=lambda item: item["label"])} for name, items in sorted(groups.items())], "capacity": 6}


@app.get("/api/admin/dashboard-settings")
async def dashboard_settings() -> dict:
    candidates = _dashboard_candidates()
    settings = _settings_for(candidates)
    return {**settings.as_dict(), "capacity": 6}


@app.put("/api/admin/dashboard-settings")
async def update_dashboard_settings(request: Request) -> dict:
    candidates = _dashboard_candidates()
    try:
        payload = await request.json()
        # The existing preset editor predates exhibition settings.  Preserve
        # demo state when it submits its legacy payload; the dedicated toggle
        # is the sole owner of this setting.
        if isinstance(payload, dict) and "demo" not in payload:
            current = _settings_for(candidates)
            payload = {**payload, "demo": {"enabled": current.demo_enabled}}
        settings = get_settings_repository().save_payload(
            payload, _available_display_groups([item for item in candidates if item.selectable]), display_item_migrations(candidates),
        )
    except SettingsError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, ValueError) as error:
        raise HTTPException(500, "表示設定を保存できません") from error
    return {**settings.as_dict(), "capacity": 6}


@app.post("/api/admin/dashboard-settings/mode")
async def update_dashboard_mode(request: Request) -> dict:
    candidates = _dashboard_candidates()
    try:
        mode = (await request.json()).get("mode")
        if mode not in {"recommended", "custom", "clock"}:
            raise SettingsError("この表示モードはまだ利用できません")
        settings = _settings_for(candidates)
        selectable = [item for item in candidates if item.selectable]
        # Mode changes only select a stored preset.  The explicit refresh action
        # is responsible for replacing the recommendation snapshot.
        recommended = settings.recommended_blocks
        clock_items = clock_item_ids(selectable) if mode == "clock" else settings.clock_item_ids
        updated = DashboardSettings(mode, settings.custom_blocks, recommended, clock_items, settings.demo_enabled)
        get_settings_repository().save(updated, _available_display_groups(candidates))
    except (SettingsError, ValueError, AttributeError) as error:
        raise HTTPException(400, str(error)) from error
    return {**updated.as_dict(), "capacity": 6}


@app.post("/api/admin/dashboard-settings/recommended")
async def refresh_recommended_dashboard() -> dict:
    candidates = _dashboard_candidates()
    try:
        settings = _settings_for(candidates)
        updated = DashboardSettings("recommended", settings.custom_blocks, tuple(recommended_blocks([item for item in candidates if item.selectable])), settings.clock_item_ids, settings.demo_enabled)
        get_settings_repository().save(updated, _available_display_groups(candidates))
    except (SettingsError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    return {**updated.as_dict(), "capacity": 6}

@app.get("/api/admin/nodes")
async def nodes() -> dict:
    return await _ble_request("GET", "/api/nodes")

@app.post("/api/admin/nodes/{node_id}/register", status_code=202)
async def register_node(node_id: str, request: Request) -> dict:
    return await _ble_request("POST", f"/api/nodes/{node_id}/register", await request.json())

@app.delete("/api/admin/nodes/{node_id}/registration")
async def remove_node_registration(node_id: str) -> dict:
    return await _ble_request("DELETE", f"/api/nodes/{node_id}/registration")


@app.get("/api/admin/setup/usb-nodes")
async def usb_nodes() -> dict:
    return await _system_manager_request("GET", "/api/nodes/usb-candidates")


@app.post("/api/admin/setup/usb-setup", status_code=202)
async def setup_usb_node(request: Request) -> dict:
    return await _system_manager_request("POST", "/api/nodes/usb-setup", await request.json())


@app.get("/api/admin/setup/usb-setup/status")
async def usb_setup_status() -> dict:
    return await _system_manager_request("GET", "/api/nodes/usb-setup/status")


@app.post("/api/admin/setup/usb-provision")
async def provision_usb_node(request: Request) -> dict:
    return await _system_manager_request("POST", "/api/nodes/usb-provision", await request.json())


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
async def suggested_sensor_id(device_key: str, confirmed_model: str | None = None) -> dict:
    params = {"device_key": device_key}
    if confirmed_model is not None:
        params["confirmed_model"] = confirmed_model
    return await _ble_request("GET", "/api/setup/suggested-sensor-id?" + urlencode(params))


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

@app.get("/api/export/usb/status")
async def usb_export_status() -> dict:
    return await _system_manager_request("GET", "/api/export/usb/status")

@app.post("/api/export/usb", status_code=202)
async def start_usb_export(request: Request) -> dict:
    return await _system_manager_request("POST", "/api/export/usb", await request.json())


@app.get("/api/admin/access-point")
async def access_point_status() -> dict:
    return await _system_manager_request("GET", "/api/access-point/status")


def _wifi_qr_escape(value: str) -> str:
    return "".join(f"\\{character}" if character in r'\\;,:\"' else character for character in value)


@app.post("/api/admin/access-point/reveal")
async def reveal_access_point_credentials() -> dict[str, str]:
    credentials = await _system_manager_request("GET", "/api/access-point/credentials")
    ssid, password = credentials.get("ssid"), credentials.get("password")
    if not isinstance(ssid, str) or not isinstance(password, str):
        raise HTTPException(502, "OMKアクセスポイント設定の応答が不正です")
    payload = f"WIFI:T:WPA;S:{_wifi_qr_escape(ssid)};P:{_wifi_qr_escape(password)};;"
    image = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    svg = image.to_string(encoding="unicode")
    return {"ssid": ssid, "password": password, "qr_svg": svg}


def _demo_enabled() -> bool:
    return _settings_for(_dashboard_candidates()).demo_enabled


async def _demo_outdoor_device_ids() -> frozenset[str]:
    """Read location metadata when available; demo never requires the manager.

    A waterproof model or a th-* ID alone does not establish outdoor placement.
    The generic collector catalog intentionally does not contain that metadata.
    """
    candidate_ids = {item.device_id for item in _dashboard_candidates()
                     if item.semantic_role in {"temperature", "humidity"}}
    if not candidate_ids:
        return frozenset()
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            response = await client.get(f"{BLE_MANAGER_URL}/api/sensors")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return frozenset()
    sensors = payload.get("sensors") if isinstance(payload, dict) else None
    if not isinstance(sensors, list):
        return frozenset()
    return frozenset(
        sensor["sensor_id"] for sensor in sensors
        if isinstance(sensor, dict) and isinstance(sensor.get("sensor_id"), str)
        and sensor["sensor_id"] in candidate_ids and sensor.get("enabled", True) is True
        and sensor.get("sensor_type") == "environment" and sensor.get("location") == "屋外"
    )


@app.get("/api/display")
async def display_api(demo_mode: str | None = None) -> dict:
    """Return the current dashboard snapshot for in-page refreshes."""
    if demo_mode is not None and demo_mode not in {"custom", "clock", "recommended"}:
        raise HTTPException(400, "表示モードが正しくありません")
    enabled = _demo_enabled()
    outdoor_device_ids = await _demo_outdoor_device_ids() if enabled and demo_mode in {None, "custom"} else frozenset()
    return {**get_dashboard_view_model((demo_mode or "custom") if enabled else None, outdoor_device_ids=outdoor_device_ids).as_dict(), "demo_enabled": enabled}


@app.post("/api/admin/dashboard-settings/demo")
async def update_dashboard_demo(request: Request) -> dict:
    candidates = _dashboard_candidates()
    try:
        enabled = (await request.json()).get("enabled")
        if not isinstance(enabled, bool):
            raise SettingsError("展示用デモモードの指定が正しくありません")
        settings = _settings_for(candidates)
        updated = DashboardSettings(settings.mode, settings.custom_blocks, settings.recommended_blocks, settings.clock_item_ids, enabled)
        get_settings_repository().save(updated, _available_display_groups(candidates))
    except (SettingsError, ValueError, AttributeError) as error:
        raise HTTPException(400, str(error)) from error
    return {**updated.as_dict(), "capacity": 6}


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
