import asyncio
import json
import re
import shutil
import stat
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
from app.data.display_repository import DisplayRepository
from app.data.settings_repository import DisplayBlock, DisplaySelection, SettingsError, SettingsRepository
from app.display_items import candidate_for, catalog_items_with_latest, selected_blocks, selected_items
from app.metric_definitions import definition_for, format_value
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


def _write_generic_item(root: Path, suffix: str, *, topic: str, device_id: str, field: str, value: object, value_type: str = "number", received_at: datetime = NOW) -> str:
    item_id = f"item_v1_{suffix * 64}"
    item = {"version": 1, "id": item_id, "topic": topic, "device_id": device_id, "field": field, "value_type": value_type, "value": value, "measured_at": None, "received_at": received_at.isoformat(), "source": {"qos": 0, "retain": False}}
    (root / "items").mkdir(parents=True, exist_ok=True)
    (root / "items" / f"{item_id}.json").write_text(json.dumps(item), encoding="utf-8")
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"version": 1, "items": []}
    catalog["items"].append({key: item[key] for key in ("id", "topic", "device_id", "field", "value_type")} | {"last_received_at": item["received_at"]})
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    return item_id


def _block_payload(*blocks: dict) -> dict:
    return {"version": 2, "default_preset": "standard", "presets": {"standard": {"blocks": list(blocks)}}}


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


def test_hero_display_html_and_javascript_expose_polling_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    item_ids = [
        _write_generic_item(
            latest_root, "h", topic="omk/living/environment", device_id="living",
            field="temperature_c", value=25.4, received_at=datetime.now(JST),
        ),
        _write_generic_item(
            latest_root, "i", topic="omk/living/environment", device_id="living",
            field="relative_humidity_percent", value=48.0, received_at=datetime.now(JST),
        ),
        _write_generic_item(
            latest_root, "j", topic="omk/living/environment", device_id="living",
            field="co2_ppm", value=650, received_at=datetime.now(JST),
        ),
    ]
    settings_path = tmp_path / "dashboard" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(_block_payload({
        "block_id": "block_sen66", "group": "living", "title": "SEN66", "size": "large", "layout_pattern": "hero",
        "primary_item_id": item_ids[0], "item_ids": item_ids,
    })), encoding="utf-8")
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(settings_path))
    response = client.get("/display")
    javascript = (Path(__file__).parents[1] / "app" / "static" / "display.js").read_text(encoding="utf-8")
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    for element_id in ("header-date-main", "header-weekday", "header-time", "updated-at", "freshness", "display-blocks"):
        assert f'id="{element_id}"' in response.text
    assert 'class="admin-link"' in response.text
    assert '<svg viewBox="0 0 24 24"' in response.text
    assert 'data-block-id="block_sen66"' in response.text
    for item_id in item_ids:
        assert f'data-item-id="{item_id}"' in response.text
    assert "display-card--large" in response.text
    assert "display-card--hero" in response.text
    assert 'class="display-card-primary"' in response.text
    assert 'class="display-card-secondary"' in response.text
    assert 'fetch("/api/display", { cache: "no-store" })' in javascript
    assert "DISPLAY_POLL_INTERVAL_MS = 10_000" in javascript
    assert "headerWeekday.textContent" in javascript
    assert 'WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]' in javascript
    assert 'data.mode === "standard"' in javascript
    assert "Array.isArray(data.blocks)" in javascript
    assert "data-item-id" in javascript
    assert "item.short_label || item.label" in javascript
    assert ".standard-grid" in stylesheet
    assert ".display-card--large" in stylesheet
    assert ".display-card--medium" in stylesheet
    assert ".display-card--small" in stylesheet
    assert ".display-card--hero" in stylesheet
    assert ".display-card--strip" in stylesheet
    assert "grid-template-columns: minmax(0, 1.1fr) minmax(260px, .9fr);" in stylesheet
    assert ".admin-link svg { width: clamp(28px, 3vw, 36px);" in stylesheet
    assert "font-variant-numeric: tabular-nums" in stylesheet


def test_strip_and_compact_display_html_use_their_own_dom_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    strip_ids = [
        _write_generic_item(latest_root, suffix, topic="omk/sen66/environment", device_id="sen66", field=field, value=value, received_at=datetime.now(JST))
        for suffix, field, value in (("k", "temperature_c", 25.4), ("l", "relative_humidity_percent", 48.0), ("m", "co2_ppm", 650))
    ]
    compact_id = _write_generic_item(latest_root, "n", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=12.3, received_at=datetime.now(JST))
    settings_path = tmp_path / "dashboard" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(_block_payload(
        {"block_id": "strip_sen66", "group": "sen66", "title": "SEN66", "size": "medium", "layout_pattern": "strip", "primary_item_id": strip_ids[0], "item_ids": strip_ids},
        {"block_id": "compact_plug", "group": "plug-001", "title": "Plug", "size": "small", "layout_pattern": "compact", "primary_item_id": compact_id, "item_ids": [compact_id]},
    )), encoding="utf-8")
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(settings_path))

    response = client.get("/display")

    assert response.status_code == 200
    assert 'data-block-id="strip_sen66"' in response.text
    assert "display-card--medium" in response.text
    assert "display-card--strip" in response.text
    assert 'class="display-card-strip-items"' in response.text
    for item_id in strip_ids:
        assert f'data-item-id="{item_id}"' in response.text
    assert 'data-block-id="compact_plug"' in response.text
    assert "display-card--small" in response.text
    assert "display-card--compact" in response.text
    assert f'data-item-id="{compact_id}"' in response.text


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


def test_generic_catalog_candidates_group_and_format_values(tmp_path: Path) -> None:
    broute_id = _write_generic_item(tmp_path, "a", topic="omk/broute-001/power", device_id="broute-001", field="net_power_w", value=1234)
    plug_id = _write_generic_item(tmp_path, "b", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=12.3)
    ichijo_id = _write_generic_item(tmp_path, "c", topic="omk/ichijo-001/power-flow", device_id="ichijo-001", field="pv_power_w", value=800)
    status_id = _write_generic_item(tmp_path, "d", topic="omk/broute-001/status", device_id="broute-001", field="status", value="online", value_type="string")
    repository = DisplayRepository(tmp_path)
    candidates = {item.id: candidate_for(item) for item in repository.catalog()}

    assert repository.item(broute_id) is not None
    assert candidates[broute_id].group == "電力メーター（Bルート）"
    assert candidates[plug_id].group == "plug-001"
    assert candidates[ichijo_id].group == "太陽光・蓄電池"
    assert candidates[status_id].selectable is False
    assert definition_for("unknown_scalar").selectable is True
    assert definition_for("net_power_w").semantic_role == "grid_power"
    assert definition_for("net_power_w").unit == "kW"
    assert format_value(1103, definition_for("load_power_w")) == "1.10"
    assert format_value(23, definition_for("net_power_w")) == "0.02"
    assert definition_for("power_w").semantic_role == "device_power"
    assert definition_for("power_w").unit == "W"
    assert format_value(4.1, definition_for("power_w")) == "4.1"
    assert format_value(True, definition_for("switch_state")) == "ON"
    display_candidates = {item.id: item for item in catalog_items_with_latest(repository, NOW)}
    assert (display_candidates[broute_id].value, display_candidates[broute_id].unit) == ("1.23", "kW")
    assert (display_candidates[plug_id].value, display_candidates[plug_id].unit) == ("12.3", "W")


def test_settings_validate_capacity_duplicates_and_persist_atomically(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    groups = {"item_a": "source_a", "item_b": "source_a", "item_c": "source_b"}
    settings = repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "large", "primary_item_id": "item_a", "item_ids": ["item_a", "item_b"]}), groups)
    assert settings.blocks[0].item_ids == ("item_a", "item_b")
    assert settings.blocks[0].layout_pattern == "hero"
    assert repository.load_or_create(groups, []).blocks == settings.blocks
    assert stat.S_IMODE((tmp_path / "dashboard" / "settings.json").stat().st_mode) == 0o644
    assert not list((tmp_path / "dashboard").glob("*.tmp"))
    with pytest.raises(SettingsError, match="複数ブロック"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "primary_item_id": "item_a", "item_ids": ["item_a"]}, {"block_id": "b", "group": "source_a", "title": "B", "size": "small", "primary_item_id": "item_a", "item_ids": ["item_a"]}), groups)
    with pytest.raises(SettingsError, match="存在しない"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "primary_item_id": "missing", "item_ids": ["missing"]}), groups)
    with pytest.raises(SettingsError, match="表示領域"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "large", "primary_item_id": "item_a", "item_ids": ["item_a"]}, {"block_id": "b", "group": "source_b", "title": "B", "size": "large", "primary_item_id": "item_c", "item_ids": ["item_c"]}, {"block_id": "c", "group": "source_a", "title": "C", "size": "small", "primary_item_id": "item_b", "item_ids": ["item_b"]}), groups)
    with pytest.raises(SettingsError):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "huge", "primary_item_id": "item_a", "item_ids": ["item_a"]}), groups)
    with pytest.raises(SettingsError, match="項目数"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "layout_pattern": "compact", "primary_item_id": "item_a", "item_ids": ["item_a", "item_b", "item_c", "item_d"]}), {"item_a": "source_a", "item_b": "source_a", "item_c": "source_a", "item_d": "source_a"})


def test_catalog_candidates_include_the_same_latest_value_and_freshness_as_display(tmp_path: Path) -> None:
    item_id = _write_generic_item(tmp_path, "g", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=22.0, received_at=NOW)
    repository = DisplayRepository(tmp_path)
    candidates = {item.id: item for item in catalog_items_with_latest(repository, NOW)}
    selected = selected_items(repository, (DisplaySelection(item_id, "small"),), NOW)

    assert candidates[item_id].value == "22.0"
    assert candidates[item_id].freshness == "normal"
    assert selected[0].value == candidates[item_id].value
    assert selected[0].freshness == candidates[item_id].freshness


def test_v2_layout_pattern_is_saved_and_existing_v2_defaults_by_size(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    groups = {"temp": "SEN66", "humidity": "SEN66"}
    settings = repository.save_payload(_block_payload({
        "block_id": "sen", "group": "SEN66", "title": "SEN66", "size": "medium",
        "layout_pattern": "strip", "primary_item_id": "temp", "item_ids": ["temp", "humidity"],
    }), groups)
    assert settings.blocks[0].layout_pattern == "strip"
    assert json.loads((tmp_path / "dashboard" / "settings.json").read_text(encoding="utf-8"))["presets"]["standard"]["blocks"][0]["layout_pattern"] == "strip"

    old_v2 = _block_payload({"block_id": "old", "group": "SEN66", "title": "SEN66", "size": "large", "primary_item_id": "temp", "item_ids": ["temp"]})
    assert repository._parse(old_v2, groups, allow_missing=False)[0].blocks[0].layout_pattern == "hero"


def test_v1_settings_migrate_to_grouped_blocks(tmp_path: Path) -> None:
    path = tmp_path / "dashboard" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "default_preset": "standard", "presets": {"standard": {"items": [
        {"item_id": "temp", "size": "small"}, {"item_id": "humidity", "size": "small"}, {"item_id": "grid", "size": "large"},
    ]}}}), encoding="utf-8")
    settings = SettingsRepository(path).load_or_create({"temp": "SEN66", "humidity": "SEN66", "grid": "電力メーター（Bルート）"}, [])

    assert [(block.group, block.item_ids, block.primary_item_id) for block in settings.blocks] == [
        ("SEN66", ("temp", "humidity"), "temp"), ("電力メーター（Bルート）", ("grid",), "grid"),
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_display_block_resolves_primary_and_multiple_secondary_values(tmp_path: Path) -> None:
    item_ids = [
        _write_generic_item(tmp_path, "v", topic="omk/sen66/environment", device_id="sen66", field="temperature_c", value=26.8, received_at=NOW),
        _write_generic_item(tmp_path, "w", topic="omk/sen66/environment", device_id="sen66", field="relative_humidity_percent", value=40, received_at=NOW),
        _write_generic_item(tmp_path, "x", topic="omk/sen66/environment", device_id="sen66", field="co2_ppm", value=520, received_at=NOW),
    ]
    block = DisplayBlock("sen66", "sen66", "SEN66", "medium", item_ids[0], tuple(item_ids), "strip")
    rendered = selected_blocks(DisplayRepository(tmp_path), (block,), NOW)

    assert rendered[0].primary.value == "26.8"
    assert [item.value for item in rendered[0].secondary] == ["40", "520"]
    assert [item.short_label for item in (rendered[0].primary, *rendered[0].secondary)] == ["温度", "湿度", "CO₂"]
    assert rendered[0].as_dict()["size"] == "medium"
    assert rendered[0].as_dict()["layout_pattern"] == "strip"


def test_energy_blocks_keep_multiple_broute_and_ichijo_values(tmp_path: Path) -> None:
    grid = _write_generic_item(tmp_path, "y", topic="omk/broute/power", device_id="broute", field="net_power_w", value=770, received_at=NOW)
    imported = _write_generic_item(tmp_path, "z", topic="omk/broute/energy", device_id="broute", field="import_energy_kwh", value=2.5, received_at=NOW)
    load = _write_generic_item(tmp_path, "0", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1103, received_at=NOW)
    pv = _write_generic_item(tmp_path, "1", topic="omk/ichijo/power-flow", device_id="ichijo", field="pv_power_w", value=1210, received_at=NOW)
    repository = DisplayRepository(tmp_path)
    blocks = selected_blocks(repository, (
        DisplayBlock("broute", "電力メーター（Bルート）", "Bルート", "small", grid, (grid, imported)),
        DisplayBlock("ichijo", "太陽光・蓄電池", "一条パワコン", "large", load, (load, pv)),
    ), NOW)

    assert blocks[0].primary.unit == "kW" and blocks[0].secondary[0].unit == "kWh"
    assert (blocks[1].primary.value, blocks[1].secondary[0].value) == ("1.10", "1.21")


def test_ichijo_charge_and_discharge_are_one_dashboard_only_battery_row(tmp_path: Path) -> None:
    load = _write_generic_item(tmp_path, "a", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1103, received_at=NOW)
    pv = _write_generic_item(tmp_path, "b", topic="omk/ichijo/power-flow", device_id="ichijo", field="pv_power_w", value=520, received_at=NOW)
    charge = _write_generic_item(tmp_path, "c", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_charge_power_w", value=0, received_at=NOW)
    discharge = _write_generic_item(tmp_path, "d", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_discharge_power_w", value=590, received_at=NOW)
    grid = _write_generic_item(tmp_path, "e", topic="omk/ichijo/power-flow", device_id="ichijo", field="grid_import_power_w", value=0, received_at=NOW)
    block = DisplayBlock("ichijo", "太陽光・蓄電池", "一条パワコン", "large", load, (load, pv, charge, discharge, grid), "hero")
    rendered = selected_blocks(DisplayRepository(tmp_path), (block,), NOW)[0]

    assert rendered.layout_pattern == "hero"
    assert rendered.primary.short_label == "家庭消費電力"
    assert [(item.semantic_role, item.short_label, item.value) for item in rendered.secondary] == [
        ("pv_power", "PV発電", "0.52"),
        ("battery_power_bidirectional", "蓄電池 放電", "0.59"),
        ("grid_import", "買電電力", "0.00"),
    ]


def test_display_items_api_lists_multiple_values_from_one_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    broute_fields = ("net_power_w", "cumulative_energy_import_kwh", "cumulative_energy_export_kwh")
    for suffix, field in zip(("m", "n", "o"), broute_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/broute-001/power", device_id="broute-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    ichijo_fields = ("pv_power_w", "load_power_w", "battery_soc_percent")
    for suffix, field in zip(("p", "q", "r"), ichijo_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/ichijo-001/power-flow", device_id="ichijo-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    sen66_fields = ("temperature_c", "relative_humidity_percent", "co2_ppm")
    for suffix, field in zip(("s", "t", "u"), sen66_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/sen66-001/environment", device_id="sen66-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))

    response = client.get("/api/admin/display-items")

    assert response.status_code == 200
    groups = {group["name"]: group["items"] for group in response.json()["groups"]}
    broute = groups["電力メーター（Bルート）"]
    assert {item["field"] for item in broute} == set(broute_fields)
    assert all(item["selectable"] for item in broute)
    assert {item["field"] for item in groups["太陽光・蓄電池"]} == set(ichijo_fields)
    assert {item["field"] for item in groups["sen66-001"]} == set(sen66_fields)


def test_admin_display_css_allows_vertical_scroll_without_changing_kiosk_overflow() -> None:
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")
    admin_template = (Path(__file__).parents[1] / "app" / "templates" / "admin_display.html").read_text(encoding="utf-8")
    display_template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")

    assert "html.admin-document { height: auto; min-height: 100%; overflow-x: hidden; overflow-y: scroll;" in stylesheet
    assert "html.admin-document body.admin-body { min-height: 100vh; min-height: 100dvh; height: auto; overflow: visible; }" in stylesheet
    assert "html.admin-document::-webkit-scrollbar { width: 12px; }" in stylesheet
    assert ".display-settings-page { width: min(100%, 1180px); height: auto; min-height: 100dvh; max-height: none;" in stylesheet
    assert ".display-settings-page { padding: 14px; }" in stylesheet
    assert ".display-settings-intro { display: grid; grid-template-columns: minmax(0, 1fr) auto;" in stylesheet
    assert "min-height: 46px;" in stylesheet
    assert '<html lang="ja" class="admin-document">' in admin_template
    assert '<body class="admin-body">' in admin_template
    assert 'href="/display">ダッシュボードを確認</a>' in admin_template
    assert '<body class="admin-body">' not in display_template
    assert "overflow: hidden;" in stylesheet


def test_dynamic_display_api_uses_selected_order_and_keeps_unavailable_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    fresh_id = _write_generic_item(latest_root, "e", topic="omk/living/environment", device_id="living", field="temperature_c", value=25.4, received_at=datetime.now(JST))
    old_id = _write_generic_item(latest_root, "f", topic="omk/bedroom/environment", device_id="bedroom", field="temperature_c", value=23.0, received_at=datetime.now(JST) - timedelta(seconds=601))
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    put = client.put("/api/admin/dashboard-settings", json=_block_payload(
        {"block_id": "old", "group": "bedroom", "title": "寝室", "size": "small", "primary_item_id": old_id, "item_ids": [old_id]},
        {"block_id": "fresh", "group": "living", "title": "SEN66", "size": "large", "primary_item_id": fresh_id, "item_ids": [fresh_id]},
    ))
    assert put.status_code == 200
    snapshot = client.get("/api/display").json()
    assert snapshot["mode"] == "standard"
    assert [block["id"] for block in snapshot["blocks"]] == ["old", "fresh"]
    assert snapshot["blocks"][0]["primary"]["value"] == "--"
    assert snapshot["blocks"][0]["primary"]["freshness"] == "unavailable"
    assert snapshot["blocks"][1]["size"] == "large"


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


@pytest.mark.parametrize(
    ("status", "payload", "text", "expected"),
    [
        (404, {"detail": "node was not found"}, "", "node was not found"),
        (422, {"detail": [{"loc": ["body", "logical_id"], "msg": "invalid value"}]}, "", "logical_id: invalid value"),
        (500, ValueError(), "BLE manager unavailable", "BLE manager unavailable"),
        (500, ValueError(), "", "BLE manager error (HTTP 500)"),
    ],
)
def test_ble_proxy_preserves_status_and_safely_formats_backend_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: object, text: str, expected: str,
) -> None:
    class ErrorResponse:
        status_code = status

        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

        @property
        def text(self) -> str:
            return text

    class ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, *_: object, **__: object):
            return ErrorResponse()

    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", lambda **_: ErrorClient())
    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._ble_request("POST", "/api/nodes/x/register", {"logical_id": "bad"}))
    assert error.value.status_code == status
    assert error.value.detail == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_display_settings_ui_shows_all_source_items_and_marks_selected() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin_display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {innerHTML: "", textContent: "", disabled: false, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, closest() { return null; }}; }
const elements = Object.fromEntries(["#selected-items", "#available-items", "#capacity-status", "#settings-status", "#save-settings"].map(key => [key, element()]));
global.document = {querySelector: selector => elements[selector]};
global.fetch = async url => ({ok: true, json: async () => url.endsWith("display-items") ? {
  capacity: 6,
  groups: [
    {name: "電力メーター（Bルート）", items: [
      {id: "grid", label: "Bルート 系統電力", group: "電力メーター（Bルート）", selectable: true},
      {id: "import-total", label: "Bルート 買電積算", group: "電力メーター（Bルート）", selectable: true},
      {id: "export-total", label: "Bルート 売電積算", group: "電力メーター（Bルート）", selectable: true},
    ]},
    {name: "太陽光・蓄電池", items: [
      {id: "load", label: "一条パワコン 家庭消費電力", group: "太陽光・蓄電池", selectable: true},
      {id: "pv", label: "一条パワコン PV発電", group: "太陽光・蓄電池", selectable: true},
      {id: "soc", label: "一条パワコン 蓄電池残量", group: "太陽光・蓄電池", selectable: true},
    ]},
    {name: "SEN66", items: [
      {id: "temperature", label: "sen66 温度", group: "SEN66", selectable: true},
      {id: "humidity", label: "sen66 湿度", group: "SEN66", selectable: true},
      {id: "co2", label: "sen66 CO₂", group: "SEN66", selectable: true},
    ]},
    {name: "plug-001", items: [
      {id: "plug-power", label: "plug-001 消費電力", group: "plug-001", selectable: true},
      {id: "plug-state", label: "plug-001 状態", group: "plug-001", selectable: true},
    ]},
    {name: "th-001", items: [{id: "th-temperature", label: "th-001 温度", group: "th-001", selectable: true}]},
  ],
} : {presets: {standard: {blocks: [
  {block_id: "b", group: "電力メーター（Bルート）", title: "Bルート", size: "large", primary_item_id: "grid", item_ids: ["grid"]},
  {block_id: "p", group: "太陽光・蓄電池", title: "一条パワコン", size: "small", primary_item_id: "load", item_ids: ["load"]},
  {block_id: "s", group: "SEN66", title: "SEN66", size: "small", primary_item_id: "temperature", item_ids: ["temperature"]},
]}}}});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setImmediate(() => {
  const changeMembership = (blockId, itemId, checked) => elements["#selected-items"].listeners.change({target: {dataset: {membership: `${blockId}:${itemId}`}, checked}});
  changeMembership("p", "pv", true);
  changeMembership("s", "humidity", true);
  changeMembership("s", "co2", true);
  const availableWithSpace = elements["#available-items"].innerHTML;
  elements["#available-items"].listeners.click({target: {closest() { return {dataset: {addBlock: "plug-001"}}; }}});
  const availableAtCapacity = elements["#available-items"].innerHTML;
  console.log(JSON.stringify({selected: elements["#selected-items"].innerHTML, availableWithSpace, availableAtCapacity, status: elements["#settings-status"].textContent}));
});
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)

    assert all(group in result["availableWithSpace"] for group in ("電力メーター（Bルート）", "太陽光・蓄電池", "SEN66"))
    assert all(label in result["availableWithSpace"] for label in (
        "Bルート 系統電力", "Bルート 買電積算", "Bルート 売電積算",
        "一条パワコン 家庭消費電力", "一条パワコン PV発電", "一条パワコン 蓄電池残量",
        "sen66 温度", "sen66 湿度", "sen66 CO₂",
    ))
    assert "このデータでブロックを追加" in result["availableWithSpace"]
    assert "一条パワコン PV発電" in result["selected"]
    assert "sen66 CO₂" in result["selected"]
    assert "表示形式" in result["selected"]
    assert "横長一覧" in result["selected"]
    assert "このブロックに表示する値（3 / 3）" in result["selected"]
    assert "plug-001 消費電力" in result["selected"]
    assert "th-001 温度" in result["availableAtCapacity"]
    assert "表示領域がいっぱいです" in result["availableAtCapacity"]
    assert result["status"] == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_node_registration_ui_formats_errors_and_renders_state_transitions() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return { hidden: false, textContent: "", className: "", value: "", disabled: false, onclick: null, onsubmit: null, addEventListener() {}, closest() { return null; }, querySelector() { return element(); }, querySelectorAll() { return []; }, focus() { global.document.activeElement = this; }, setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }, showModal() {}, close() {} }; }
const selectors = ["#setup-status", "#candidates", "#registered-sensors", "#omk-nodes", "#start-scan", "#stop-scan", "#register-dialog", "#register-form", "#register-error", "#edit-dialog", "#edit-form", "#edit-error", "#cancel-register", "#cancel-edit", "#delete-sensor"];
const elements = Object.fromEntries(selectors.map(key => [key, element()]));
global.document = {activeElement: null, querySelector: selector => elements[selector] || element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: []})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const provisioned = nodeCard({node_id: "112233445566", registration_state: "provisioned", capabilities: ["ble_scan"]});
const requested = nodeCard({node_id: "112233445566", registration_state: "provisioned", request_state: "request_sent", capabilities: ["ble_scan"]});
const registeredMarkup = nodeCard({node_id: "112233445566", registration_state: "registered", logical_id: "ble-relay-001", capabilities: ["ble_scan"]});
const oldInput = Object.assign(element(), {dataset: {nodeId: "112233445566"}, value: "ble-relay-001", selectionStart: 4, selectionEnd: 7});
const newInput = Object.assign(element(), {dataset: {nodeId: "112233445566"}, value: "", selectionStart: 0, selectionEnd: 0});
elements["#omk-nodes"].querySelectorAll = () => [oldInput];
elements["#omk-nodes"].querySelector = () => newInput;
global.document.activeElement = oldInput;
const saved = saveNodeInputState();
restoreNodeInputState(saved);
const inputForSubmit = Object.assign(element(), {value: ""});
const submitButton = Object.assign(element(), {dataset: {nodeId: "112233445566"}, closest() { return {querySelector() { return inputForSubmit; }}; }});
const requests = [];
global.fetch = async (url, options = {}) => { requests.push({url, options}); return {ok: true, json: async () => url.endsWith("/nodes") ? {nodes: [{node_id: "112233445566", registration_state: "registered", logical_id: "ble-relay-001"}]} : {status: "request_sent"}}; };
(async () => {
  await submitNodeRegistration(submitButton);
  const emptyDoesNotPost = requests.length === 0;
  inputForSubmit.value = "bad id";
  await submitNodeRegistration(submitButton);
  const invalidDoesNotPost = requests.length === 0;
  inputForSubmit.value = "ble-relay-001";
  await submitNodeRegistration(submitButton);
  const validPosts = requests[0]?.url === "/api/admin/nodes/112233445566/register" && JSON.parse(requests[0].options.body).logical_id === "ble-relay-001";
  console.log(JSON.stringify({
  string: formatApiError("bad request"),
  object: formatApiError({loc: ["body", "logical_id"], msg: "invalid value"}),
  list: formatApiError([{loc: ["body", "logical_id"], msg: "invalid value"}]),
  provisioned: provisioned.includes("register-node"),
  requested: requested.includes("登録要求を送信済み") && !requested.includes("register-node"),
  registered: registeredMarkup.includes("登録済み") && registeredMarkup.includes("ble-relay-001") && !registeredMarkup.includes("register-node"),
  preservedValue: newInput.value === "ble-relay-001",
  preservedFocus: global.document.activeElement === newInput && newInput.selectionStart === 4 && newInput.selectionEnd === 7,
  logicalIdValidation: !validLogicalId("") && !validLogicalId("bad id") && validLogicalId("ble-relay-001"),
  emptyDoesNotPost,
  invalidDoesNotPost,
  validPosts,
  }));
})();
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {
        "string": "bad request",
        "object": "logical_id: invalid value",
        "list": "logical_id: invalid value",
        "provisioned": True,
        "requested": True,
        "registered": True,
        "preservedValue": True,
        "preservedFocus": True,
        "logicalIdValidation": True,
        "emptyDoesNotPost": True,
        "invalidDoesNotPost": True,
        "validPosts": True,
    }


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


@pytest.mark.parametrize(
    ("dashboard_path", "system_manager_path"),
    [
        ("/api/admin/system/reboot", "/api/system/reboot"),
        ("/api/admin/system/shutdown", "/api/system/shutdown"),
    ],
)
def test_system_power_proxy_uses_fixed_upstream_endpoint(
    monkeypatch: pytest.MonkeyPatch, dashboard_path: str, system_manager_path: str
) -> None:
    async def accepted(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("POST", system_manager_path, None)
        return {"accepted": True}

    monkeypatch.setattr(dashboard_main, "_system_manager_request", accepted)
    response = client.post(dashboard_path)

    assert response.status_code == 200
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
    assert "設定を保存しました。Bルートへの接続を開始します。" in javascript
    assert "設定は保存されましたが、Bルートサービスの再起動に失敗しました。" in javascript


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for system controls tests")
def test_system_controls_require_confirmation_before_fixed_power_requests() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "system.js"
    response = client.get("/admin/system")
    assert response.status_code == 200
    assert "再起動" in response.text
    assert "シャットダウン" in response.text
    assert 'id="system-reboot"' in response.text
    assert 'id="system-shutdown"' in response.text
    assert 'class="system-confirm-title"' in response.text
    assert 'class="dialog-device system-confirm-message"' in response.text

    harness = r'''
const fs = require("fs"), vm = require("vm");
const elements = {};
function element() { return { textContent: "", disabled: false, listeners: {}, open: false, addEventListener(type, listener) { this.listeners[type] = listener; }, showModal() { this.open = true; }, close() { this.open = false; } }; }
for (const selector of ["#system-reboot", "#system-shutdown", "#system-message", "#system-confirm", "#system-confirm-message", "#system-confirm-cancel", "#system-confirm-execute"]) elements[selector] = element();
const requests = []; let return503 = false;
global.document = {querySelector: (selector) => elements[selector]};
global.window = {setTimeout() {}, location: {assign() {}}};
global.fetch = async (url, options) => { requests.push({url, options}); return {ok: !return503, status: return503 ? 503 : 200, json: async () => return503 ? {detail: "システム管理サービスに接続できません"} : {accepted: true}}; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  elements["#system-reboot"].listeners.click();
  const rebootConfirmation = elements["#system-confirm"].open && elements["#system-confirm-message"].textContent === "OMKを再起動しますか？";
  elements["#system-confirm-cancel"].listeners.click();
  const cancelDoesNotRequest = requests.length === 0 && !elements["#system-confirm"].open;
  elements["#system-shutdown"].listeners.click();
  const shutdownConfirmation = elements["#system-confirm-message"].textContent.includes("電源を入れ直すまで利用できません");
  await elements["#system-confirm-execute"].listeners.click();
  const shutdownRequested = requests.length === 1 && requests[0].url === "/api/admin/system/shutdown" && requests[0].options.method === "POST" && elements["#system-message"].textContent === "OMKをシャットダウンしています…";
  elements["#system-reboot"].listeners.click(); return503 = true;
  await elements["#system-confirm-execute"].listeners.click();
  const interruptedRebootIsNotError = elements["#system-message"].textContent === "OMKを再起動しています…";
  console.log(JSON.stringify({rebootConfirmation, cancelDoesNotRequest, shutdownConfirmation, shutdownRequested, interruptedRebootIsNotError}));
})();
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True
    )
    assert json.loads(completed.stdout) == {
        "rebootConfirmation": True,
        "cancelDoesNotRequest": True,
        "shutdownConfirmation": True,
        "shutdownRequested": True,
        "interruptedRebootIsNotError": True,
    }


def test_admin_menu_has_only_available_management_functions() -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert "<title>OMK 管理メニュー</title>" in response.text
    assert "<h1>管理メニュー</h1>" in response.text
    assert "Bルート設定" in response.text
    assert "システム操作" in response.text
    assert "ネットワーク" not in response.text
    assert "システム情報" not in response.text
    assert "準備中" not in response.text


def test_management_subpages_link_back_to_admin_menu() -> None:
    for path in ("/admin/sensors", "/admin/broute", "/admin/system"):
        response = client.get(path)

        assert response.status_code == 200
        assert "← 管理メニュー" in response.text


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
const elements = {}, documentListeners = {};
function element() { return { value: "", selectionStart: 0, disabled: false, className: "", textContent: "", hidden: false, children: [], listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; }, append(child) { this.children.push(child); }, setAttribute() {}, setSelectionRange(position) { this.selectionStart = position; }, reset() { elements["#broute-id"].value = ""; elements["#broute-password"].value = ""; } }; }
for (const selector of ["#broute-form", "#broute-id", "#broute-password", "#broute-save", "#broute-retry", "#broute-message", "#broute-connection", "#broute-id-masked", "#broute-password-configured", "#broute-keyboard-overlay", "#broute-keyboard-title", "#broute-keyboard-count", "#broute-keyboard-value", "#broute-keyboard-keys", "#broute-keyboard-cancel", "#broute-keyboard-confirm"]) elements[selector] = element();
elements["#broute-keyboard-overlay"].hidden = true;
global.document = { querySelector: (selector) => elements[selector], createElement: () => element(), addEventListener(type, listener) { documentListeners[type] = listener; } };
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
  function physicalKey(key) { let prevented = false; documentListeners.keydown({key, preventDefault() { prevented = true; }}); return prevented; }
  identifier.value = "";
  identifier.listeners.pointerdown({preventDefault() {}});
  physicalKey("a");
  __brouteTest.appendKeyboardKey("B");
  physicalKey("3");
  const mixedKeyboardInput = identifier.value === "aB3" && elements["#broute-keyboard-value"].textContent === "aB3";
  for (let index = 0; index < 40; index += 1) physicalKey("z");
  const physicalIdMaximum = __brouteTest.unformatToken(identifier.value, 32).length === 32;
  physicalKey("Backspace");
  const physicalBackspace = __brouteTest.unformatToken(identifier.value, 32).length === 31;
  physicalKey("Escape");
  const physicalEscapeRestored = overlay.hidden && identifier.value === "";
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
  passInput.value = "ab";
  passInput.listeners.pointerdown({preventDefault() {}});
  physicalKey("c");
  const physicalEnter = physicalKey("Enter") && overlay.hidden && passInput.value === "abc";
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
  console.log(JSON.stringify({ formattedId: __brouteTest.formatToken(id, 32), formattedPassword: __brouteTest.formatToken(password, 12), normalizedPaste: __brouteTest.unformatToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validPassword: __brouteTest.validToken("1234 5678 9ABC", 12), invalidShortId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF", 32), masked: __brouteTest.formatToken("0000************************4CEF", 32), idOpened, passwordOpened, idMaximum, passwordMaximum, idBackspace, cancellationRestored, mixedKeyboardInput, physicalIdMaximum, physicalBackspace, physicalEscapeRestored, physicalEnter, confirmed, noPutBeforeSave, passwordBlankOnLoad, stateLabels, retryButtonVisibleAfterRepeatedFailures, retryButtonHiddenBeforeExtendedRetry, retryButtonHiddenWhenAdapterMissing, missingStateIsNotStarting, adapterMissingMessage: __brouteTest.stateMessage("adapter_missing"), adapterInitializingMessage: __brouteTest.stateMessage("adapter_initializing"), scanErrorMessage: __brouteTest.stateMessage("scan_error"), retryWaitMessage: __brouteTest.stateMessage("retry_wait", 30), idlePolling, upstreamErrorIsNotTransport, transportError, reconnectStates, scanErrorTerminal, authenticationErrorTerminal, connectionErrorTerminal, adapterMissingStatus, longRetryWaitDoesNotTimeout, request: JSON.parse(put.options.body) }));
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
    assert result["mixedKeyboardInput"] is True
    assert result["physicalIdMaximum"] is True
    assert result["physicalBackspace"] is True
    assert result["physicalEscapeRestored"] is True
    assert result["physicalEnter"] is True
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
        {"state": "stopped", "label": "再起動中", "delay": 1500, "message": "Bルートへ接続しています。"},
        {"state": "starting", "label": "起動中", "delay": 1500, "message": "Bルートへ接続しています。"},
        {"state": "scanning", "label": "スマートメータを探索中", "delay": 1500, "message": "Bルートへ接続しています。"},
        {"state": "retry_wait", "label": "再試行待ち", "delay": 10000, "message": "30秒後にスマートメータを再探索します。"},
        {"state": "authenticating", "label": "認証中", "delay": 1500, "message": "Bルートへ接続しています。"},
        {"state": "connected", "label": "接続済み", "delay": 10000, "message": "Bルートへ接続しました。"},
    ]
    assert result["scanErrorTerminal"] is True
    assert result["authenticationErrorTerminal"] is True
    assert result["connectionErrorTerminal"] is True
    assert result["adapterMissingStatus"] is True
    assert result["longRetryWaitDoesNotTimeout"] is True
    assert result["request"] == {"id": "123456789ABCDEF0123456789ABCDEF0", "password": "123456789ABC"}


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200
