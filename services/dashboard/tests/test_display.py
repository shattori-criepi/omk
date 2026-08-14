import asyncio
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from fastapi.testclient import TestClient

import app.main as dashboard_main
from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import LatestPower, ParquetRepository
from app.main import app
from app.view_models import (
    FreshnessStatus,
    PowerDirection,
    format_power,
    freshness_for,
    get_display_view_model,
)

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 7, 30, 12, 0, 30, tzinfo=JST)
client = TestClient(app)


def _write_parquet(root: Path, dataset: str, columns: str, rows: list[tuple]) -> None:
    destination = root / dataset / "date=2026-07-30" / "data.parquet"
    destination.parent.mkdir(parents=True)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"CREATE TABLE records ({columns})")
        placeholders = ", ".join("?" for _ in rows[0])
        connection.executemany(f"INSERT INTO records VALUES ({placeholders})", rows)
        connection.execute(f"COPY records TO '{destination}' (FORMAT PARQUET)")
    finally:
        connection.close()


def _write_energy_data(root: Path) -> None:
    _write_parquet(
        root,
        "broute_interval_energy",
        "device_id VARCHAR, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, import_energy_kwh DOUBLE, export_energy_kwh DOUBLE, quality_status VARCHAR",
        [
            ("broute-001", datetime(2026, 7, 30, 11, 0, tzinfo=JST), datetime(2026, 7, 30, 11, 30, tzinfo=JST), 0.2, 0.4, "normal"),
            ("broute-001", datetime(2026, 7, 30, 11, 30, tzinfo=JST), datetime(2026, 7, 30, 12, 0, tzinfo=JST), 0.5, 0.9, "estimated"),
            ("broute-001", datetime(2026, 7, 29, 23, 0, tzinfo=JST), datetime(2026, 7, 29, 23, 30, tzinfo=JST), 9.0, 9.0, "normal"),
        ],
    )


def _write_latest(root: Path, filename: str, payload: dict, received_at: datetime = NOW) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": received_at.isoformat(timespec="milliseconds"),
        "topic": "omk/test-001/example",
        "qos": 0,
        "retain": False,
        "payload": payload,
    }
    (root / filename).write_text(json.dumps(record), encoding="utf-8")


def _write_instantaneous_data(
    root: Path,
    *,
    power_at: datetime | None = None,
    sen66_at: datetime | None = None,
    ichijo_at: datetime | None = None,
) -> None:
    _write_latest(
        root,
        "broute_power.json",
        {
            "measured_at": (power_at or datetime(2026, 7, 30, 12, 0, 20, tzinfo=JST)).isoformat(),
            "net_power_w": -3581.0,
        },
    )
    _write_latest(
        root,
        "sen66.json",
        {
            "temperature_celsius": 26.44,
            "relative_humidity_percent": 48.4,
            "co2_ppm": 1200.0,
            "pm2_5_ug_m3": 20.2,
            "voc_index": 123.4,
        },
        sen66_at or datetime(2026, 7, 30, 12, 0, 0, tzinfo=JST),
    )
    if ichijo_at is not None:
        _write_latest(
            root,
            "ichijo_power_flow.json",
            {
                "measured_at": ichijo_at.isoformat(),
                "load_power_w": 1500.0,
                "pv_power_w": 800.0,
                "grid_import_power_w": 700.0,
                "grid_export_power_w": 0.0,
                "battery_soc_percent": 65.0,
                "battery_charge_power_w": 100.0,
                "battery_discharge_power_w": 0.0,
                "battery_operating_state": "charging",
            },
        )


def test_latest_repository_reads_instantaneous_values(tmp_path: Path) -> None:
    _write_instantaneous_data(tmp_path, ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST))

    repository = LatestRepository(tmp_path)
    power = repository.latest_power()
    sen66 = repository.latest_sen66()
    ichijo = repository.latest_ichijo_power_flow()

    assert power is not None and power.net_power_w == -3581.0
    assert sen66 is not None and sen66.measured_at == datetime(2026, 7, 30, 12, 0, tzinfo=JST)
    assert sen66.temperature_c == 26.44
    assert ichijo is not None and ichijo.load_power_w == 1500.0
    assert ichijo.battery_operating_state == "charging"


def test_view_model_uses_latest_values_and_parquet_today_energy(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    processed_root = tmp_path / "processed"
    _write_instantaneous_data(latest_root)
    _write_energy_data(processed_root)

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(processed_root), now=NOW)

    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_direction == "売電"
    assert dashboard.power_flow == "sale"
    assert dashboard.temperature_c == "26.4"
    assert dashboard.humidity_percent == "48"
    assert dashboard.co2_ppm == "1200"
    assert dashboard.pm25_ug_m3 == "20.2"
    assert dashboard.voc_index == "123"
    assert dashboard.purchased_today_kwh == "0.7"
    assert dashboard.sold_today_kwh == "1.3"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == "normal"
    assert dashboard.power_freshness == FreshnessStatus.NORMAL
    assert dashboard.sen66_freshness == FreshnessStatus.NORMAL


def test_stale_ichijo_falls_back_to_broute_power(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(latest_root, ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST))

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_direction == "売電"


def test_delayed_ichijo_keeps_values_and_marks_power_source(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is True
    assert dashboard.current_power_kw == "1.50"
    assert dashboard.pv_power_kw == "0.80"
    assert dashboard.power_freshness == FreshnessStatus.DELAYED


def test_unavailable_ichijo_and_broute_hides_power_value(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "-"
    assert dashboard.power_freshness == FreshnessStatus.UNAVAILABLE


def test_delayed_broute_keeps_value_when_ichijo_is_unavailable(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_freshness == FreshnessStatus.DELAYED


def test_sen66_delayed_and_unavailable_are_isolated_from_power(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
    )

    delayed = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)
    assert delayed.temperature_c == "26.4"
    assert delayed.sen66_freshness == FreshnessStatus.DELAYED
    assert delayed.current_power_kw == "1.50"

    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )
    unavailable = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)
    assert unavailable.temperature_c == "-"
    assert unavailable.humidity_percent == "-"
    assert unavailable.voc_index == "-"
    assert unavailable.sen66_freshness == FreshnessStatus.UNAVAILABLE
    assert unavailable.current_power_kw == "1.50"


def test_fresh_ichijo_excludes_stale_broute_from_global_status(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 0, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 12, 0, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is True
    assert dashboard.current_power_label == "現在の消費電力"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == FreshnessStatus.NORMAL


def test_stale_ichijo_uses_fresh_broute_for_global_status(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 12, 0, 20, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 12, 0, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_label == "現在の売電"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == FreshnessStatus.NORMAL


def test_broken_or_missing_latest_data_does_not_break_display(tmp_path: Path, monkeypatch) -> None:
    latest_root = tmp_path / "latest"
    latest_root.mkdir()
    (latest_root / "broute_power.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path / "processed"))

    assert LatestRepository(latest_root).latest_power() is None
    response = client.get("/display")

    assert response.status_code == 200
    assert re.search(r'<strong id="current-power-kw">-</strong>', response.text)
    assert re.search(
        r'id="power-source-badge"[^>]*source-badge--unavailable', response.text
    )
    assert re.search(r'<strong id="temperature-c">-</strong>', response.text)
    assert re.search(
        r'id="sen66-source-badge"[^>]*source-badge--unavailable', response.text
    )
    assert "取得不可" in response.text
    assert "データなし" not in response.text


def test_display_api_returns_ichijo_and_broute_fallback_snapshots(tmp_path: Path, monkeypatch) -> None:
    latest_root = tmp_path / "latest"
    current_time = datetime.now(JST)
    _write_latest(
        latest_root,
        "broute_power.json",
        {"measured_at": current_time.isoformat(), "net_power_w": 1240.0},
        current_time,
    )
    _write_latest(
        latest_root,
        "sen66.json",
        {
            "temperature_celsius": 25.0,
            "relative_humidity_percent": 45.0,
            "co2_ppm": 600.0,
            "pm2_5_ug_m3": 3.0,
            "voc_index": 90.0,
        },
        current_time,
    )
    _write_latest(
        latest_root,
        "ichijo_power_flow.json",
        {
            "measured_at": current_time.isoformat(),
            "load_power_w": 1500.0,
            "pv_power_w": 800.0,
            "grid_import_power_w": 700.0,
            "grid_export_power_w": 0.0,
            "battery_soc_percent": 65.0,
            "battery_charge_power_w": 100.0,
            "battery_discharge_power_w": 0.0,
            "battery_operating_state": "charging",
        },
        current_time,
    )
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path / "processed"))

    response = client.get("/api/display")

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["has_ichijo_power_flow"] is True
    assert snapshot["current_power_label"] == "現在の消費電力"
    assert snapshot["grid_flow_label"] == "買電中"
    assert snapshot["purchased_today_kwh"] == "0.0"
    assert snapshot["power_freshness"] == "normal"
    assert snapshot["sen66_freshness"] == "normal"

    normal_html = client.get("/display").text
    assert 'source-badge--normal" hidden' in normal_html
    assert "取得不可" not in normal_html
    sen66_section = normal_html.index('<section id="sen66-section"')
    sen66_section_end = normal_html.index("</section>", sen66_section)
    purchased_today = normal_html.index('id="purchased-today-kwh"')
    sen66_badge = normal_html.index('id="sen66-source-badge"')
    temperature_card = normal_html.index("<p>温度</p>")
    assert not sen66_section < purchased_today < sen66_section_end
    assert normal_html[sen66_section:sen66_section_end].count('<article class="card">') == 5
    assert sen66_section < sen66_badge < temperature_card
    assert 'id="sen66-source-badge"' not in normal_html[
        normal_html.index("<p>温度</p>") : normal_html.index("</article>", temperature_card)
    ]
    assert normal_html.index('id="purchased-today-kwh"') < normal_html.index('id="sold-today-kwh"')
    assert normal_html.index('id="sold-today-kwh"') < normal_html.index('id="pv-power-kw"')
    assert normal_html.index('id="pv-power-kw"') < normal_html.index('id="battery-soc-percent"')
    assert normal_html.index('id="battery-soc-percent"') < normal_html.index('id="battery-power-kw"')
    assert normal_html.count('class="power-detail-row"') == 5
    assert normal_html.count('class="power-detail-value"') == 5
    assert normal_html.count('class="power-detail-unit"') == 5
    assert "today-energy-card" not in normal_html
    assert snapshot["freshness"] in {"normal", "delayed", "unavailable"}

    _write_latest(
        latest_root,
        "ichijo_power_flow.json",
        {
            "measured_at": (current_time - timedelta(seconds=601)).isoformat(),
            "load_power_w": 1500.0,
            "pv_power_w": 800.0,
            "grid_import_power_w": 700.0,
            "grid_export_power_w": 0.0,
            "battery_soc_percent": 65.0,
            "battery_charge_power_w": 100.0,
            "battery_discharge_power_w": 0.0,
            "battery_operating_state": "charging",
        },
        current_time,
    )

    fallback = client.get("/api/display").json()
    assert fallback["has_ichijo_power_flow"] is False
    assert fallback["current_power_label"] == "現在の買電"
    assert fallback["power_direction"] == "買電"


def test_display_html_and_javascript_expose_polling_targets() -> None:
    response = client.get("/display")
    javascript = (Path(__file__).parents[1] / "app" / "static" / "display.js").read_text(encoding="utf-8")
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    for element_id in (
        "header-date-main", "header-weekday", "header-time",
        "current-power-kw", "current-power-label", "power-direction", "grid-flow",
        "pv-power-kw", "battery-soc-percent", "battery-power-kw", "purchased-today-kwh",
        "sold-today-kwh", "temperature-c", "humidity-percent", "co2-ppm", "pm25-ug-m3",
        "voc-index", "updated-at", "freshness",
        "power-source-badge", "sen66-source-badge",
    ):
        assert f'id="{element_id}"' in response.text
    assert 'fetch("/api/display", { cache: "no-store" })' in javascript
    assert "DISPLAY_POLL_INTERVAL_MS = 10_000" in javascript
    assert "headerWeekday.textContent" in javascript
    assert 'WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]' in javascript
    assert ".power-detail-row" in stylesheet
    assert "font-variant-numeric: tabular-nums" in stylesheet
    assert ".today-energy-card" not in stylesheet
    assert "取得不可" in javascript
    assert "source--unavailable" in javascript


def test_power_direction_rules() -> None:
    assert format_power(LatestPower(NOW, 1240.0)) == ("1.24", "買電", PowerDirection.PURCHASE)
    assert format_power(LatestPower(NOW, 0.0)) == ("0.00", "収支なし", PowerDirection.NEUTRAL)


def test_freshness_status_boundaries() -> None:
    assert freshness_for(NOW, NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 11, 54, 30, tzinfo=JST), NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 11, 54, 29, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 11, 50, 30, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST), NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(None, NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(datetime(2026, 7, 30, 12, 1, tzinfo=JST), NOW) == FreshnessStatus.NORMAL


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_sensor_proxy_forwards_device_key_and_propagates_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def successful_request(method: str, path: str, body: dict | None = None) -> dict:
        calls.append((method, path))
        assert body is None
        return {"deleted": True, "device_key": "switchbot:plug", "sensor_id": "plug-001"}

    monkeypatch.setattr(dashboard_main, "_ble_request", successful_request)
    assert asyncio.run(dashboard_main.delete_sensor("switchbot:plug")) == {"deleted": True, "device_key": "switchbot:plug", "sensor_id": "plug-001"}
    assert calls == [("DELETE", "/api/sensors/switchbot:plug")]

    async def missing_request(_method: str, _path: str, _body: dict | None = None) -> dict:
        raise dashboard_main.HTTPException(404, "sensor was not found")

    monkeypatch.setattr(dashboard_main, "_ble_request", missing_request)
    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main.delete_sensor("switchbot:missing"))
    assert error.value.status_code == 404


def test_broute_status_proxy_returns_only_safe_status(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_identifier = "A" * 32
    raw_password = "B" * 12

    async def successful_request(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("GET", "/api/broute/credentials/status", None)
        return {
            "configured": True,
            "id_masked": "AAAA************************AAAA",
            "password_configured": True,
            "service_active": True,
        }

    monkeypatch.setattr(dashboard_main, "_system_manager_request", successful_request)
    response = client.get("/api/admin/broute-credentials")

    assert response.status_code == 200
    assert response.json()["id_masked"] == "AAAA************************AAAA"
    assert raw_identifier not in response.text
    assert raw_password not in response.text


def test_broute_update_proxy_forwards_body_and_preserves_safe_failure_state(monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = "A" * 32
    password = "B" * 12

    async def restart_failure(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path) == ("PUT", "/api/broute/credentials")
        assert body == {"id": identifier, "password": password}
        raise dashboard_main.HTTPException(
            502,
            {"code": "credentials_saved_restart_failed", "credentials_saved": True},
        )

    monkeypatch.setattr(dashboard_main, "_system_manager_request", restart_failure)
    response = client.put(
        "/api/admin/broute-credentials",
        json={"id": identifier, "password": password},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "credentials_saved_restart_failed",
        "credentials_saved": True,
    }
    assert identifier not in response.text
    assert password not in response.text


def test_broute_retry_proxy_uses_fixed_upstream_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def accepted(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("POST", "/api/broute/retry", None)
        return {"accepted": True}

    monkeypatch.setattr(dashboard_main, "_system_manager_request", accepted)
    response = client.post("/api/admin/broute-retry")

    assert response.json() == {"accepted": True}


@pytest.mark.parametrize("status_code", [401, 403, 400])
def test_broute_proxy_propagates_system_manager_errors(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    async def failed_request(*_: object, **__: object) -> dict:
        raise dashboard_main.HTTPException(status_code, "upstream error")

    monkeypatch.setattr(dashboard_main, "_system_manager_request", failed_request)
    response = client.get("/api/admin/broute-credentials")

    assert response.status_code == status_code
    assert response.json() == {"detail": "upstream error"}


def test_broute_proxy_reports_missing_system_manager_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", None)

    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert error.value.status_code == 503


def test_broute_proxy_translates_system_manager_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnreachableClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, *_: object, **__: object) -> object:
            raise dashboard_main.httpx.ConnectError("unreachable")

    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", "token-canary")
    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", lambda **_: UnreachableClient())

    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert error.value.status_code == 503


def test_broute_proxy_adds_bearer_token_only_to_host_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class SuccessfulClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: object):
            captured.update({"method": method, "url": url, **kwargs})
            return type("Response", (), {"status_code": 200, "json": lambda self: {"configured": False}})()

    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", "token-canary")
    monkeypatch.setattr(
        dashboard_main.httpx,
        "AsyncClient",
        lambda **kwargs: captured.update({"client": kwargs}) or SuccessfulClient(),
    )
    response = asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert response == {"configured": False}
    assert captured["url"] == "http://host.docker.internal:8788/api/broute/credentials/status"
    assert captured["headers"] == {"Authorization": "Bearer token-canary"}
    assert captured["client"] == {"timeout": 60}


def test_broute_admin_page_keeps_credentials_and_token_out_of_html() -> None:
    token = "dashboard-token-canary"
    raw_identifier = "A" * 32
    response = client.get("/admin/broute")
    javascript = (Path(__file__).parents[1] / "app" / "static" / "broute.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "Bルート設定" in response.text
    assert 'id="broute-password"' in response.text
    assert 'autocomplete="new-password"' in response.text
    assert 'id="broute-id" name="id" required maxlength="39"' in response.text
    assert 'id="broute-password" name="password" type="password" required maxlength="14"' in response.text
    assert '<label for="broute-id">BルートID</label>' in response.text
    assert '<label for="broute-password">パスワード</label>' in response.text
    assert "接続状態" in response.text
    assert 'id="broute-keyboard-overlay"' in response.text
    assert 'id="broute-keyboard-value"' in response.text
    assert 'id="broute-keyboard-cancel"' in response.text
    assert 'id="broute-keyboard-confirm"' in response.text
    assert token not in response.text + javascript
    assert raw_identifier not in response.text + javascript
    assert "validToken(id, 32)" in javascript
    assert "validToken(pass, 12)" in javascript
    assert "設定を保存しました。Bルートへ再接続しています。" in javascript
    assert "設定は保存されましたが、Bルートサービスの再起動に失敗しました。" in javascript


def test_broute_layout_uses_wide_grid_rows_with_narrow_screen_fallback() -> None:
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert ".broute-field { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr);" in stylesheet
    assert ".broute-status-list div { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr);" in stylesheet
    assert "@media (max-width: 700px)" in stylesheet
    assert ".broute-status-list div, .broute-field { grid-template-columns: 1fr;" in stylesheet
    assert ".broute-keyboard-overlay { position: fixed;" in stylesheet
    assert ".broute-keyboard-actions { display: grid; grid-template-columns: 1fr 1fr;" in stylesheet


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser formatter tests")
def test_broute_browser_formatter_groups_normalizes_and_submits_unformatted_values() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "broute.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
const elements = {};
function element() { return { value: "", selectionStart: 0, disabled: false, className: "", textContent: "", hidden: false, children: [], listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; }, append(child) { this.children.push(child); }, setAttribute() {}, setSelectionRange(position) { this.selectionStart = position; }, reset() { elements["#broute-id"].value = ""; elements["#broute-password"].value = ""; } }; }
for (const selector of ["#broute-form", "#broute-id", "#broute-password", "#broute-save", "#broute-retry", "#broute-message", "#broute-connection", "#broute-id-masked", "#broute-password-configured", "#broute-keyboard-overlay", "#broute-keyboard-title", "#broute-keyboard-count", "#broute-keyboard-value", "#broute-keyboard-keys", "#broute-keyboard-cancel", "#broute-keyboard-confirm"]) elements[selector] = element();
elements["#broute-keyboard-overlay"].hidden = true;
global.document = { querySelector: (selector) => elements[selector], createElement: () => element() };
const requests = [];
let response = { status: 200, detail: { configured: true, id_masked: "0000************************4CEF", password_configured: true, service_active: true, connection_state: "starting" } }, scheduledDelay = null;
let currentNow = 0;
Date.now = () => currentNow;
global.setTimeout = (_callback, delay) => { scheduledDelay = delay; return 1; };
global.clearTimeout = () => {};
global.fetch = async (url, options = {}) => { requests.push({url, options}); return { status: response.status, ok: response.status < 400, json: async () => response.detail }; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__brouteTest = {formatToken, unformatToken, validToken, appendKeyboardKey, backspaceKeyboardKey, showStatus, stateMessage, pollStatus, loadStatus};");
(async () => {
  const id = "123456789ABCDEF0123456789ABCDEF0", password = "123456789ABC";
  const identifier = elements["#broute-id"], passInput = elements["#broute-password"], overlay = elements["#broute-keyboard-overlay"];
  const passwordBlankOnLoad = passInput.value === "";
  identifier.value = "1234 5678";
  identifier.listeners.pointerdown({preventDefault() {}});
  const idOpened = !overlay.hidden && elements["#broute-keyboard-title"].textContent === "BルートID";
  __brouteTest.appendKeyboardKey("A");
  __brouteTest.backspaceKeyboardKey();
  elements["#broute-keyboard-cancel"].listeners.click();
  const cancellationRestored = overlay.hidden && identifier.value === "1234 5678";
  identifier.value = "";
  identifier.listeners.pointerdown({preventDefault() {}});
  for (let index = 0; index < 40; index += 1) __brouteTest.appendKeyboardKey("A");
  const idMaximum = __brouteTest.unformatToken(identifier.value, 32).length === 32 && identifier.value === "AAAA AAAA AAAA AAAA AAAA AAAA AAAA AAAA";
  __brouteTest.backspaceKeyboardKey();
  const idBackspace = __brouteTest.unformatToken(identifier.value, 32).length === 31;
  elements["#broute-keyboard-cancel"].listeners.click();
  passInput.value = "1234";
  passInput.listeners.pointerdown({preventDefault() {}});
  const passwordOpened = !overlay.hidden && elements["#broute-keyboard-title"].textContent === "パスワード";
  for (let index = 0; index < 12; index += 1) __brouteTest.appendKeyboardKey("B");
  const passwordMaximum = __brouteTest.unformatToken(passInput.value, 12).length === 12 && elements["#broute-keyboard-value"].textContent === "1234 BBBB BBBB";
  elements["#broute-keyboard-confirm"].listeners.click();
  const confirmed = overlay.hidden && passInput.value === "1234 BBBB BBBB";
  const noPutBeforeSave = !requests.some((request) => request.options.method === "PUT");
  const stateLabels = {};
  for (const state of ["starting", "adapter_missing", "adapter_initializing", "scanning", "authenticating", "connected", "retry_wait", "scan_error", "authentication_error", "connection_error", "status_unavailable"]) { __brouteTest.showStatus({service_active: true, connection_state: state}); stateLabels[state] = elements["#broute-connection"].textContent; }
  __brouteTest.showStatus({service_active: true, connection_state: "retry_wait", connection_attempt: 3});
  const retryButtonVisibleAfterRepeatedFailures = !elements["#broute-retry"].hidden;
  __brouteTest.showStatus({service_active: true, connection_state: "retry_wait", connection_attempt: 2});
  const retryButtonHiddenBeforeExtendedRetry = elements["#broute-retry"].hidden;
  __brouteTest.showStatus({service_active: true, connection_state: "adapter_missing", connection_attempt: 3});
  const retryButtonHiddenWhenAdapterMissing = elements["#broute-retry"].hidden;
  const missingStateIsNotStarting = __brouteTest.showStatus({service_active: true}) === "status_unavailable" && elements["#broute-connection"].textContent === "状態確認中";
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "scanning" } };
  await __brouteTest.pollStatus();
  const idlePolling = elements["#broute-connection"].textContent === "スマートメータを探索中" && scheduledDelay === 10000;
  response = { status: 400, detail: {detail: "入力値が不正です"} };
  await __brouteTest.loadStatus();
  const upstreamErrorIsNotTransport = elements["#broute-connection"].textContent === "状態を取得できません";
  response = { status: 503, detail: {detail: "システム管理サービスに接続できません"} };
  await __brouteTest.loadStatus();
  const transportError = elements["#broute-connection"].textContent === "system-manager接続エラー";
  response = { status: 200, detail: { configured: true, id_masked: "0000************************4CEF", password_configured: true, service_active: true, connection_state: "starting" } };
  elements["#broute-id"].value = "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0";
  elements["#broute-password"].value = "1234 5678 9ABC";
  await elements["#broute-form"].onsubmit({preventDefault() {}});
  const reconnectStates = [];
  for (const state of ["stopped", "starting", "scanning", "retry_wait", "authenticating", "connected"]) {
    response = { status: 200, detail: { configured: true, service_active: state !== "stopped", connection_state: state, retry_after_seconds: state === "retry_wait" ? 30 : null } };
    await __brouteTest.pollStatus();
    reconnectStates.push({state, label: elements["#broute-connection"].textContent, delay: scheduledDelay, message: elements["#broute-message"].textContent});
  }
  async function submitReconnect() {
    elements["#broute-id"].value = "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0";
    elements["#broute-password"].value = "1234 5678 9ABC";
    response = { status: 200, detail: { configured: true, service_active: false, connection_state: "stopped" } };
    await elements["#broute-form"].onsubmit({preventDefault() {}});
  }
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "scan_error" } };
  await __brouteTest.pollStatus();
  const scanErrorTerminal = elements["#broute-message"].textContent === "スマートメータを検出できませんでした。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "authentication_error" } };
  await __brouteTest.pollStatus();
  const authenticationErrorTerminal = elements["#broute-message"].textContent === "認証エラー。BルートIDまたはパスワードを確認してください。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "connection_error" } };
  await __brouteTest.pollStatus();
  const connectionErrorTerminal = elements["#broute-message"].textContent === "スマートメータとの通信に失敗しました。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "adapter_missing", connection_attempt: 3 } };
  await __brouteTest.pollStatus();
  const adapterMissingStatus = elements["#broute-connection"].textContent === "Bルートアダプターが接続されていません。" && elements["#broute-message"].textContent === "Bルートアダプターを接続してください。" && elements["#broute-retry"].hidden && scheduledDelay === 10000;
  await submitReconnect();
  currentNow = 180001;
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "retry_wait", retry_after_seconds: 300, connection_attempt: 3 } };
  await __brouteTest.pollStatus();
  const longRetryWaitDoesNotTimeout = elements["#broute-connection"].textContent === "再試行待ち" && elements["#broute-message"].textContent === "スマートメータを検出できません。BルートID・パスワード、または通信状態を確認してください。" && !elements["#broute-retry"].hidden && scheduledDelay === 10000;
  const put = requests.find((request) => request.options.method === "PUT");
  console.log(JSON.stringify({ formattedId: __brouteTest.formatToken(id, 32), formattedPassword: __brouteTest.formatToken(password, 12), normalizedPaste: __brouteTest.unformatToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validPassword: __brouteTest.validToken("1234 5678 9ABC", 12), invalidShortId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF", 32), masked: __brouteTest.formatToken("0000************************4CEF", 32), idOpened, passwordOpened, idMaximum, passwordMaximum, idBackspace, cancellationRestored, confirmed, noPutBeforeSave, passwordBlankOnLoad, stateLabels, retryButtonVisibleAfterRepeatedFailures, retryButtonHiddenBeforeExtendedRetry, retryButtonHiddenWhenAdapterMissing, missingStateIsNotStarting, adapterMissingMessage: __brouteTest.stateMessage("adapter_missing"), adapterInitializingMessage: __brouteTest.stateMessage("adapter_initializing"), scanErrorMessage: __brouteTest.stateMessage("scan_error"), retryWaitMessage: __brouteTest.stateMessage("retry_wait", 30), idlePolling, upstreamErrorIsNotTransport, transportError, reconnectStates, scanErrorTerminal, authenticationErrorTerminal, connectionErrorTerminal, adapterMissingStatus, longRetryWaitDoesNotTimeout, request: JSON.parse(put.options.body) }));
})();
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(javascript_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["formattedId"] == "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0"
    assert result["formattedPassword"] == "1234 5678 9ABC"
    assert result["normalizedPaste"] == "123456789ABCDEF0123456789ABCDEF0"
    assert result["validId"] is True
    assert result["validPassword"] is True
    assert result["invalidShortId"] is False
    assert result["masked"] == "0000 **** **** **** **** **** **** 4CEF"
    assert result["idOpened"] is True
    assert result["passwordOpened"] is True
    assert result["idMaximum"] is True
    assert result["passwordMaximum"] is True
    assert result["idBackspace"] is True
    assert result["cancellationRestored"] is True
    assert result["confirmed"] is True
    assert result["noPutBeforeSave"] is True
    assert result["passwordBlankOnLoad"] is True
    assert result["stateLabels"] == {
        "starting": "起動中",
        "adapter_missing": "Bルートアダプターが接続されていません。",
        "adapter_initializing": "Bルートアダプターを初期化しています。",
        "scanning": "スマートメータを探索中",
        "authenticating": "認証中",
        "connected": "接続済み",
        "retry_wait": "再試行待ち",
        "scan_error": "スマートメータ未検出",
        "authentication_error": "認証エラー",
        "connection_error": "通信エラー",
        "status_unavailable": "状態確認中",
    }
    assert result["missingStateIsNotStarting"] is True
    assert result["retryButtonVisibleAfterRepeatedFailures"] is True
    assert result["retryButtonHiddenBeforeExtendedRetry"] is True
    assert result["retryButtonHiddenWhenAdapterMissing"] is True
    assert result["adapterMissingMessage"] == "Bルートアダプターを接続してください。"
    assert result["adapterInitializingMessage"] == "Bルートアダプターを初期化しています。"
    assert result["scanErrorMessage"] == "スマートメータを検出できませんでした。"
    assert result["retryWaitMessage"] == "30秒後にスマートメータを再探索します。"
    assert result["idlePolling"] is True
    assert result["upstreamErrorIsNotTransport"] is True
    assert result["transportError"] is True
    assert result["reconnectStates"] == [
        {"state": "stopped", "label": "再起動中", "delay": 1500, "message": "Bルートへ再接続しています。"},
        {"state": "starting", "label": "起動中", "delay": 1500, "message": "Bルートへ再接続しています。"},
        {"state": "scanning", "label": "スマートメータを探索中", "delay": 1500, "message": "Bルートへ再接続しています。"},
        {"state": "retry_wait", "label": "再試行待ち", "delay": 10000, "message": "30秒後にスマートメータを再探索します。"},
        {"state": "authenticating", "label": "認証中", "delay": 1500, "message": "Bルートへ再接続しています。"},
        {"state": "connected", "label": "接続済み", "delay": 10000, "message": "Bルートへ接続しました。"},
    ]
    assert result["scanErrorTerminal"] is True
    assert result["authenticationErrorTerminal"] is True
    assert result["connectionErrorTerminal"] is True
    assert result["adapterMissingStatus"] is True
    assert result["longRetryWaitDoesNotTimeout"] is True
    assert result["request"] == {"id": "123456789ABCDEF0123456789ABCDEF0", "password": "123456789ABC"}


def test_dashboard_compose_uses_env_file_without_credentials_mount() -> None:
    compose = Path(__file__).parents[3] / "compose.yaml"
    source = compose.read_text(encoding="utf-8")
    dashboard_section = source.split("  dashboard:\n", 1)[1].split("\n  sensor-collector:", 1)[0]

    assert "OMK_SYSTEM_MANAGER_URL: http://host.docker.internal:8788" in dashboard_section
    assert "- /etc/omk/dashboard-system-manager.env" in dashboard_section
    assert "credentials.yaml" not in dashboard_section


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200
