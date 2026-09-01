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
    unknown = _record("omk/test-001/raw", {"device_id": "test-001", "value": "not a supported measurement"})
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


def test_interval_energy_is_partitioned_by_end_at_and_readable_by_duckdb(tmp_path):
    input_file = tmp_path / "30.jsonl"
    interval = _record("omk/broute-001/interval-energy", {
        "device_id": "broute-001", "start_at": "2026-07-30T23:30:00+09:00",
        "end_at": "2026-07-31T00:00:00+09:00", "import_energy_kwh": 0.0,
        "export_energy_kwh": 2.6, "quality_status": "normal",
    })
    input_file.write_text(json.dumps(interval) + "\n", encoding="utf-8")
    output = tmp_path / "processed"
    assert transform(input_file, output).converted == 1
    assert transform(input_file, output).converted == 1
    parquet = output / "broute_interval_energy/date=2026-07-31/data.parquet"
    table = pq.read_table(parquet)
    assert table.num_rows == 1
    assert table.schema.field("start_at").type.tz == "Asia/Tokyo"
    assert table.schema.field("end_at").type.tz == "Asia/Tokyo"
    assert table.to_pylist()[0]["export_energy_kwh"] == 2.6
    assert table.to_pylist()[0]["quality_status"] == "normal"
    assert duckdb.connect().execute("SELECT count(*), sum(export_energy_kwh) FROM read_parquet(?)", [str(parquet)]).fetchone() == (1, 2.6)


def test_interval_energy_validation_errors(tmp_path):
    valid = {"device_id": "broute-001", "start_at": "2026-07-30T12:30:00+09:00", "end_at": "2026-07-30T13:00:00+09:00", "import_energy_kwh": 0.0, "export_energy_kwh": 2.6, "quality_status": "normal"}
    cases = [
        ({**valid, "start_at": "not-a-date"}, "invalid_datetime"),
        ({**valid, "end_at": "not-a-date"}, "invalid_datetime"),
        ({**valid, "import_energy_kwh": "zero"}, "invalid_number"),
        ({**valid, "export_energy_kwh": "two"}, "invalid_number"),
        ({key: value for key, value in valid.items() if key != "quality_status"}, "missing_required_field"),
    ]
    input_file = tmp_path / "30.jsonl"
    input_file.write_text("\n".join(json.dumps(_record("omk/broute-001/interval-energy", payload)) for payload, _ in cases) + "\n", encoding="utf-8")
    result = transform(input_file, tmp_path / "processed")
    assert result.converted == 0
    assert result.errors == {"invalid_datetime": 2, "invalid_number": 2, "missing_required_field": 1}


def test_status_topics_are_ignored_without_error_jsonl(tmp_path):
    input_file = tmp_path / "30.jsonl"
    power = _record("omk/broute-001/power", {"device_id": "broute-001", "measured_at": "2026-07-30T13:00:00+09:00", "net_power_w": 100})
    broute_status = _record("omk/broute-001/status", {"device_id": "broute-001", "status": "online"})
    sen66_status = _record("omk/sen66-001/status", {"device_id": "sen66-001", "status": "online"})
    input_file.write_text("\n".join(json.dumps(record) for record in [power, broute_status, sen66_status]) + "\n", encoding="utf-8")
    result = transform(input_file, tmp_path / "processed")
    assert result.converted == 1
    assert result.ignored == 2
    assert result.ignored_topics == {"omk/broute-001/status": 1, "omk/sen66-001/status": 1}
    assert not result.errors
    assert not (tmp_path / "errors/transform/30.jsonl").exists()


def test_ichijo_power_flow_is_written_to_partitioned_parquet(tmp_path):
    input_file = tmp_path / "04.jsonl"
    record = _record(
        "omk/ichijo-001/power-flow",
        {
            "device_id": "ichijo-001",
            "measured_at": "2026-08-04T08:38:52+09:00",
            "pv_power_w": 1610,
            "battery_soc_percent": 73,
            "battery_charge_power_w": 0,
            "battery_discharge_power_w": 87,
            "battery_operating_state": "discharging",
            "battery_operating_state_raw": 67,
            "grid_import_power_w": 3,
            "grid_export_power_w": 0,
            "pcs_ac_output_power_w": 1697,
            "load_power_w": 1700,
            "quality": "normal",
            "errors": [],
        },
        received_at="2026-08-04T08:38:51.970+09:00",
    )
    input_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

    output = tmp_path / "processed"
    result = transform(input_file, output)

    assert result.converted == 1
    assert result.written == {"ichijo_power_flow": 1}

    parquet = (
        output
        / "ichijo_power_flow"
        / "date=2026-08-04"
        / "data.parquet"
    )
    row = pq.read_table(parquet).to_pylist()[0]

    assert row["device_id"] == "ichijo-001"
    assert row["measured_at"].isoformat() == "2026-08-04T08:38:52+09:00"
    assert row["load_power_w"] == 1700
    assert row["pv_power_w"] == 1610
    assert row["battery_soc_percent"] == 73.0
    assert row["battery_discharge_power_w"] == 87
    assert row["battery_operating_state"] == "discharging"
    assert row["grid_import_power_w"] == 3
    assert row["grid_export_power_w"] == 0
    assert row["quality"] == "normal"


def test_ble_datasets_preserve_direct_relay_and_optional_measurements(tmp_path):
    input_file = tmp_path / "01.jsonl"
    records = [
        _record("omk/th-001/environment", {
            "device_id": "th-001", "measured_at": "2026-09-01T13:57:25+09:00",
            "quality": "normal", "temperature_c": 24.4,
            "relative_humidity_percent": 47, "source": "direct",
        }),
        _record("omk/th-002/environment", {
            "device_id": "th-002", "measured_at": "2026-09-01T13:57:26+09:00",
            "temperature_c": 25.1, "relative_humidity_percent": 48,
            "source": "relay", "relay_node_id": "9af9509eb8b6",
        }),
        _record("omk/co2-001/environment", {
            "device_id": "co2-001", "measured_at": "2026-09-01T13:57:25+09:00",
            "temperature_c": 27.9, "relative_humidity_percent": 38,
            "co2_ppm": 541, "source": "direct",
        }),
        _record("omk/motion-002/motion", {
            "device_id": "motion-002", "measured_at": "2026-09-01T13:57:28+09:00",
            "motion_state": 1,
        }),
        _record("omk/contact-001/contact", {
            "device_id": "contact-001", "measured_at": "2026-09-01T13:57:23+09:00",
            "contact_state": 1,
        }),
        _record("omk/plug-001/power", {
            "device_id": "plug-001", "measured_at": "2026-09-01T13:57:21+09:00",
            "quality": "normal", "power_w": 4.1, "switch_state": 1,
        }),
        _record("omk/broute-001/power", {
            "device_id": "broute-001", "measured_at": "2026-09-01T13:57:20+09:00",
            "net_power_w": -3581,
        }),
    ]
    input_file.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    output = tmp_path / "processed"
    result = transform(input_file, output)

    assert result.written == {
        "ble_environment": 3,
        "ble_motion": 1,
        "ble_contact": 1,
        "ble_power": 1,
        "broute_power": 1,
    }
    environments = pq.read_table(output / "ble_environment/date=2026-09-01/data.parquet").to_pylist()
    direct, relay, co2 = environments
    assert direct["co2_ppm"] is None
    assert direct["relay_node_id"] is None
    assert relay["source"] == "relay"
    assert relay["relay_node_id"] == "9af9509eb8b6"
    assert co2["co2_ppm"] == 541.0
    motion = pq.read_table(output / "ble_motion/date=2026-09-01/data.parquet").to_pylist()[0]
    assert motion["motion_state"] == 1
    assert motion["battery_percent"] is None
    assert motion["light_level"] is None
    assert pq.read_table(output / "ble_contact/date=2026-09-01/data.parquet").to_pylist()[0]["contact_state"] == 1
    assert pq.read_table(output / "ble_power/date=2026-09-01/data.parquet").to_pylist()[0]["power_w"] == 4.1
    assert pq.read_table(output / "broute_power/date=2026-09-01/data.parquet").to_pylist()[0]["net_power_w"] == -3581


def test_partition_merge_replaces_one_source_without_losing_cross_date_records(tmp_path):
    output = tmp_path / "processed"
    source_a = tmp_path / "30.jsonl"
    source_b = tmp_path / "31.jsonl"
    source_a.write_text(json.dumps(_record("omk/broute-001/power", {
        "device_id": "broute-001", "measured_at": "2026-08-30T23:59:00+09:00", "net_power_w": 10,
    })) + "\n", encoding="utf-8")
    source_b.write_text(json.dumps(_record("omk/broute-001/power", {
        "device_id": "broute-001", "measured_at": "2026-08-30T23:59:30+09:00", "net_power_w": 20,
    })) + "\n", encoding="utf-8")

    transform(source_a, output)
    transform(source_b, output)
    parquet = output / "broute_power/date=2026-08-30/data.parquet"
    rows = pq.read_table(parquet).to_pylist()
    assert {row["net_power_w"] for row in rows} == {10, 20}
    assert {row["source_file"] for row in rows} == {str(source_a), str(source_b)}

    transform(source_b, output)
    rows = pq.read_table(parquet).to_pylist()
    assert len(rows) == 2
    assert {row["net_power_w"] for row in rows} == {10, 20}
