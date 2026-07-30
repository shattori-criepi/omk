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
    _write(data_root, "sen66", [{"device_id": "sen66-001", "measured_at": _time("2026-07-30T12:00:05"), "temperature_c": 25.25, "relative_humidity_pct": 45.5, "co2_ppm": 600.0, "pm2_5_ug_m3": 3.2}, {"device_id": "sen66-001", "measured_at": _time("2026-07-30T12:00:15"), "temperature_c": 25.5, "relative_humidity_pct": 46.0, "co2_ppm": 601.0, "pm2_5_ug_m3": 3.3}])
    _write(data_root, "broute_power", [{"device_id": "broute-001", "measured_at": _time("2026-07-30T12:00:00"), "net_power_w": 100}])
    _write(data_root, "broute_cumulative_energy", [{"device_id": "broute-001", "metered_at": _time("2026-07-30T12:00:00"), "cumulative_energy_import_kwh": 10.0, "cumulative_energy_export_kwh": 5.0}, {"device_id": "broute-001", "metered_at": _time("2026-07-30T12:30:00"), "cumulative_energy_import_kwh": 11.0, "cumulative_energy_export_kwh": 5.5}])
    _write(data_root, "broute_interval_energy", [{"device_id": "broute-001", "start_at": _time("2026-07-30T12:00:00"), "end_at": _time("2026-07-30T12:30:00"), "import_energy_kwh": 1.0, "export_energy_kwh": 0.5, "quality_status": "normal"}])
    return data_root


def test_verification_succeeds_and_prints_key_sections(tmp_path, capsys):
    assert verify_parquet.run(ROOT / "scripts" / "verify_parquet.sql", _processed_data(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "SEN66: basic information" in output
    assert "hourly statistics" in output
    assert "Cumulative-energy deltas compared with interval energy" in output
    assert "ASOF join" in output


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
    assert "broute_power' is missing required columns" in capsys.readouterr().err
    assert "net_power_w" in capsys.readouterr().err
