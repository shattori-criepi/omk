import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from fastapi.testclient import TestClient

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
    assert "データなし" in response.text
    assert "unavailable" in response.text
    assert ">-<" in response.text


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

    assert response.status_code == 200
    for element_id in (
        "current-power-kw", "current-power-label", "power-direction", "grid-flow",
        "pv-power-kw", "battery-soc-percent", "battery-power-kw", "purchased-today-kwh",
        "sold-today-kwh", "temperature-c", "humidity-percent", "co2-ppm", "pm25-ug-m3",
        "voc-index", "updated-at", "freshness",
        "power-source-badge", "sen66-source-badge",
    ):
        assert f'id="{element_id}"' in response.text
    assert 'fetch("/api/display", { cache: "no-store" })' in javascript
    assert "DISPLAY_POLL_INTERVAL_MS = 10_000" in javascript
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


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200
