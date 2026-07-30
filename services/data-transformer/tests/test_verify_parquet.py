import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from data_transformer.transformer import SCHEMAS  # noqa: E402


ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts" / "verify_parquet.py"
SPEC = importlib.util.spec_from_file_location("verify_parquet", SCRIPT)
verify_parquet = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify_parquet)
JST = ZoneInfo("Asia/Tokyo")


def _time(value):
    return datetime.fromisoformat(value).replace(tzinfo=JST)


def _write(data_root, dataset, rows):
    path = data_root / dataset / "date=2026-07-30" / "data.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMAS[dataset]), path)


def _processed_data(tmp_path):
    data_root = tmp_path / "processed"
    _write(data_root, "sen66", [{"device_id": "sen66-001", "measured_at": _time("2026-07-30T11:59:50"), "temperature_c": 25.0, "relative_humidity_pct": 45.0, "co2_ppm": 599.0, "pm2_5_ug_m3": 3.1}, {"device_id": "sen66-001", "measured_at": _time("2026-07-30T12:00:05"), "temperature_c": 25.25, "relative_humidity_pct": 45.5, "co2_ppm": 600.0, "pm2_5_ug_m3": 3.2}, {"device_id": "sen66-001", "measured_at": _time("2026-07-30T12:00:10"), "temperature_c": 25.5, "relative_humidity_pct": 46.0, "co2_ppm": 601.0, "pm2_5_ug_m3": 3.3}, {"device_id": "sen66-001", "measured_at": _time("2026-07-30T12:00:20"), "temperature_c": 25.75, "relative_humidity_pct": 46.5, "co2_ppm": 602.0, "pm2_5_ug_m3": 3.4}])
    _write(data_root, "broute_power", [{"device_id": "broute-001", "measured_at": _time("2026-07-30T12:00:00"), "net_power_w": 100}, {"device_id": "broute-001", "measured_at": _time("2026-07-30T12:00:10"), "net_power_w": 110}])
    _write(data_root, "broute_cumulative_energy", [{"device_id": "broute-001", "metered_at": _time("2026-07-30T12:00:00"), "cumulative_energy_import_kwh": 10.0, "cumulative_energy_export_kwh": 5.0}, {"device_id": "broute-001", "metered_at": _time("2026-07-30T12:30:00"), "cumulative_energy_import_kwh": 11.0, "cumulative_energy_export_kwh": 5.5}])
    _write(data_root, "broute_interval_energy", [{"device_id": "broute-001", "start_at": _time("2026-07-30T11:30:00"), "end_at": _time("2026-07-30T12:00:00"), "import_energy_kwh": 0.1, "export_energy_kwh": 0.1, "quality_status": "normal"}, {"device_id": "broute-001", "start_at": _time("2026-07-30T12:00:00"), "end_at": _time("2026-07-30T12:30:00"), "import_energy_kwh": 1.0, "export_energy_kwh": 0.5, "quality_status": "normal"}])
    return data_root


def _section_row(output, title):
    section = output.split(f"=== {title} ===\n", 1)[1].split("\n=== ", 1)[0].strip().splitlines()
    return dict(zip((value.strip() for value in section[0].split("|")), (value.strip() for value in section[2].split("|")), strict=True))


def test_verification_succeeds_and_prints_key_sections(tmp_path, capsys):
    assert verify_parquet.run(ROOT / "scripts" / "verify_parquet.sql", _processed_data(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "SEN66: basic information" in output
    assert "hourly statistics" in output
    assert "Cumulative-energy deltas compared with interval energy" in output
    assert "ASOF join" in output
    asof = _section_row(output, "SEN66 and B-route power ASOF join (15 sec or less: usual; 15-30 sec: update delay; over 30 sec: stale or missing candidate)")
    assert asof["all_sen66_record_count"] == "4"
    assert asof["overlap_sen66_record_count"] == "2"
    assert asof["joined_count"] == "2"
    assert asof["unmatched_in_overlap_count"] == "0"
    assert asof["join_rate_in_overlap_pct"] == "100.0"
    assert asof["before_power_period_count"] == "1"
    assert asof["after_power_period_count"] == "1"
    interval_summary = _section_row(output, "B-route interval energy: summary")
    assert interval_summary["total_export_kwh"] == "0.6"
    assert "not_comparable" in output


def test_verification_reports_missing_dataset(tmp_path, capsys):
    assert verify_parquet.run(ROOT / "scripts" / "verify_parquet.sql", tmp_path / "processed") == 1
    assert "Required dataset 'sen66'" in capsys.readouterr().err


def test_verification_reports_sql_statement_error(tmp_path, capsys):
    sql = tmp_path / "invalid.sql"
    sql.write_text("SELECT 1;\n-- SECTION: broken\nSELECT invalid sql;", encoding="utf-8")
    assert verify_parquet.run(sql, _processed_data(tmp_path)) == 1
    assert "broken" in capsys.readouterr().err


def test_verification_reports_missing_schema_column(tmp_path, capsys):
    data_root = _processed_data(tmp_path)
    power = data_root / "broute_power/date=2026-07-30/data.parquet"
    pq.write_table(pa.table({"device_id": ["broute-001"], "measured_at": [_time("2026-07-30T12:00:00")]}), power)
    assert verify_parquet.run(ROOT / "scripts" / "verify_parquet.sql", data_root) == 1
    captured = capsys.readouterr()
    assert "broute_power' is missing required columns" in captured.err
    assert "net_power_w" in captured.err
