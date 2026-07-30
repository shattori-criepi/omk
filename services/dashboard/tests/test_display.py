from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from fastapi.testclient import TestClient

from app.data.parquet_repository import LatestPower, LatestSen66, ParquetRepository
from app.main import app
from app.view_models import (
    FreshnessStatus,
    PowerDirection,
    air_quality,
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


def _write_dashboard_data(root: Path) -> None:
    _write_parquet(
        root,
        "broute_power",
        "device_id VARCHAR, measured_at TIMESTAMPTZ, net_power_w DOUBLE",
        [
            ("broute-001", datetime(2026, 7, 30, 12, 0, 5, tzinfo=JST), 1240.0),
            ("broute-001", datetime(2026, 7, 30, 12, 0, 20, tzinfo=JST), -3581.0),
        ],
    )
    _write_parquet(
        root,
        "sen66",
        "device_id VARCHAR, measured_at TIMESTAMPTZ, temperature_c DOUBLE, relative_humidity_pct DOUBLE, co2_ppm DOUBLE, pm2_5_ug_m3 DOUBLE",
        [
            ("sen66-001", datetime(2026, 7, 30, 11, 59, 30, tzinfo=JST), 24.0, 40.0, 900.0, 10.0),
            ("sen66-001", datetime(2026, 7, 30, 12, 0, 0, tzinfo=JST), 26.44, 48.4, 1200.0, 20.2),
        ],
    )
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


def test_view_model_reads_latest_values_and_today_energy(tmp_path: Path) -> None:
    _write_dashboard_data(tmp_path)

    dashboard = get_display_view_model(ParquetRepository(tmp_path), now=NOW)

    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_direction == "売電"
    assert dashboard.power_flow == "sale"
    assert dashboard.temperature_c == "26.4"
    assert dashboard.humidity_percent == "48"
    assert dashboard.co2_ppm == "1200"
    assert dashboard.pm25_ug_m3 == "20.2"
    assert dashboard.purchased_today_kwh == "0.7"
    assert dashboard.sold_today_kwh == "1.3"
    assert dashboard.air_quality == "注意"
    assert dashboard.updated_at == "2026/07/30 12:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == "delayed"


def test_power_direction_and_air_quality_rules(tmp_path: Path) -> None:
    _write_dashboard_data(tmp_path)
    repository = ParquetRepository(tmp_path)
    dashboard = get_display_view_model(repository, now=NOW)
    assert dashboard.power_direction == "売電"
    assert air_quality(repository.latest_sen66()) == "注意"
    assert format_power(LatestPower(NOW, 1240.0)) == ("1.24", "買電", PowerDirection.PURCHASE)
    assert format_power(LatestPower(NOW, 0.0)) == ("0.00", "収支なし", PowerDirection.NEUTRAL)
    assert air_quality(LatestSen66(NOW, 25.0, 45.0, 1000.0, 15.0)) == "良好"
    assert air_quality(LatestSen66(NOW, 25.0, 45.0, 1600.0, 15.0)) == "要確認"


def test_freshness_status_boundaries() -> None:
    assert freshness_for(NOW, NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 12, 0, 15, tzinfo=JST), NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 12, 0, 14, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 12, 0, 0, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 11, 59, 59, tzinfo=JST), NOW) == FreshnessStatus.STALE
    assert freshness_for(None, NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(datetime(2026, 7, 30, 12, 1, tzinfo=JST), NOW) == FreshnessStatus.NORMAL


def test_display_handles_missing_parquet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path))

    response = client.get("/display")

    assert response.status_code == 200
    assert "データなし" in response.text
    assert "unavailable" in response.text
    assert "--" in response.text


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200
