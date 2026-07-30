import json
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from data_transformer.transformer import transform  # noqa: E402


def _record(topic, payload, received_at="2026-07-30T13:00:08.866+09:00"):
    return {"received_at": received_at, "topic": topic, "qos": 0, "retain": False, "payload": payload}


def test_transform_separates_datasets_preserves_timestamps_and_is_idempotent(tmp_path):
    input_file = tmp_path / "30.jsonl"
    records = [
        _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": -3581}),
        _record("omk/broute-001/cumulative-energy", {"device_id": "broute-001", "metered_at": "2026-07-30T13:00:00+09:00", "received_at": "2026-07-30T13:00:08.858463+09:00", "cumulative_energy_import_kwh": 9435.8, "cumulative_energy_export_kwh": 33260.4}),
        _record("omk/sen66-001/sen66", {"device_id": "sen66-001", "temperature_celsius": 25.3, "relative_humidity_percent": 48.2, "co2_ppm": 612, "pm2_5_ug_m3": 6.8, "nox_index": None}),
    ]
    input_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    output = tmp_path / "processed"
    assert transform(input_file, output).converted == 3
    assert transform(input_file, output).converted == 3
    power = output / "broute_power/date=2026-07-30/data.parquet"
    table = pq.read_table(power)
    assert table.num_rows == 1
    assert table.schema.field("measured_at").type.tz == "Asia/Tokyo"
    assert table.to_pylist()[0]["net_power_w"] == -3581
    connection = duckdb.connect()
    assert connection.execute("SELECT count(*), avg(net_power_w) FROM read_parquet(?)", [str(power)]).fetchone() == (1, -3581.0)
    sen66 = pq.read_table(output / "sen66/date=2026-07-30/data.parquet").to_pylist()[0]
    assert sen66["measured_at"].isoformat() == "2026-07-30T13:00:08.866000+09:00"
    assert sen66["temperature_c"] == 25.3


def test_invalid_records_are_reported_without_stopping(tmp_path):
    input_file = tmp_path / "30.jsonl"
    valid = _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": 100})
    malformed = '{not json}'
    bad_time = _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "today", "net_power_w": 1})
    unknown = _record("omk/a/status", {"device_id": "a", "status": "online"})
    input_file.write_text("\n".join([json.dumps(valid), malformed, json.dumps(bad_time), json.dumps(unknown)]) + "\n", encoding="utf-8")
    result = transform(input_file, tmp_path / "processed")
    assert result.converted == 1
    assert result.errors == {"invalid_json": 1, "invalid_datetime": 1, "unsupported_topic": 1}
    errors = (tmp_path / "errors/transform/30.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(errors) == 3


def test_dry_run_writes_nothing(tmp_path):
    input_file = tmp_path / "30.jsonl"
    input_file.write_text(json.dumps(_record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": 1})) + "\n")
    assert transform(input_file, tmp_path / "processed", dry_run=True).converted == 1
    assert not (tmp_path / "processed").exists()


def test_invalid_numeric_value_is_reported_and_existing_parquet_is_kept(tmp_path):
    input_file = tmp_path / "30.jsonl"
    valid = _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": 100})
    input_file.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    output = tmp_path / "processed"
    transform(input_file, output)
    parquet = output / "broute_power/date=2026-07-30/data.parquet"
    original = parquet.read_bytes()
    invalid = _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": "100"})
    input_file.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    result = transform(input_file, output)
    assert result.errors == {"invalid_number": 1}
    assert parquet.read_bytes() == original
