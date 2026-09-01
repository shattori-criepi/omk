from __future__ import annotations

import csv
from datetime import date, datetime
import io
import json
from pathlib import Path
import zipfile
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_exporter.engine import ExportError, export_parquet
from data_exporter.cli import _date


JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 9, 2, 8, 0, tzinfo=JST)


def _write(root: Path, dataset: str, partition: str, rows: list[dict]) -> None:
    destination = root / dataset / f"date={partition}" / "data.parquet"
    destination.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), destination)


def _rows(when: datetime, **extra: object) -> list[dict]:
    return [{
        "device_id": "sensor-1", "measured_at": when, "source_file": "raw.jsonl",
        "source_line_number": 12, "quality": "normal", "value": 1.5, **extra,
    }]


def _csv(archive: zipfile.ZipFile, name: str) -> list[list[str]]:
    return list(csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="")))


def test_one_dataset_one_day_omits_internal_columns_and_keeps_timezone(tmp_path):
    root = tmp_path / "processed"
    _write(root, "sen66", "2026-08-01", _rows(datetime(2026, 8, 1, 14, 32, 10, tzinfo=JST)))

    result = export_parquet(root, tmp_path / "exports", date(2026, 8, 1), date(2026, 8, 1), ["sen66"], now=NOW)

    with zipfile.ZipFile(result.path) as archive:
        rows = _csv(archive, "sen66.csv")
        assert rows[0] == ["device_id", "measured_at", "quality", "value"]
        assert rows[1][1] == "2026-08-01T14:32:10+09:00"
        assert "source_file" not in rows[0]
        assert "source_line_number" not in rows[0]
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["datasets"] == ["sen66"]
    assert manifest["from"] == "2026-08-01"
    assert manifest["to"] == "2026-08-01"
    assert manifest["row_counts"] == {"sen66": 1}
    assert not list((tmp_path / "exports").glob("*.partial"))


def test_multiple_datasets_days_limits_partitions_and_joins_ichijo_errors(tmp_path):
    root = tmp_path / "processed"
    _write(root, "sen66", "2026-07-31", _rows(datetime(2026, 7, 31, 23, tzinfo=JST)))
    _write(root, "sen66", "2026-08-01", _rows(datetime(2026, 8, 1, tzinfo=JST)))
    _write(root, "sen66", "2026-08-02", _rows(datetime(2026, 8, 2, tzinfo=JST)))
    _write(root, "sen66", "2026-08-03", _rows(datetime(2026, 8, 3, tzinfo=JST)))
    _write(root, "ichijo_power_flow", "2026-08-02", _rows(datetime(2026, 8, 2, tzinfo=JST), errors=["pv_failed", "grid_failed"]))

    result = export_parquet(root, tmp_path / "exports", date(2026, 8, 1), date(2026, 8, 2), now=NOW)

    with zipfile.ZipFile(result.path) as archive:
        assert set(archive.namelist()) == {"sen66.csv", "ichijo_power_flow.csv", "manifest.json"}
        assert len(_csv(archive, "sen66.csv")) == 3  # header + inclusive endpoints
        ichijo = _csv(archive, "ichijo_power_flow.csv")
        assert ichijo[1][ichijo[0].index("errors")] == "pv_failed;grid_failed"
    assert result.row_counts == {"sen66": 2, "ichijo_power_flow": 1}


def test_no_data_invalid_dates_and_failures_leave_no_completed_zip(tmp_path, monkeypatch):
    root = tmp_path / "processed"
    with pytest.raises(ExportError, match="ありません"):
        export_parquet(root, tmp_path / "exports", date(2026, 8, 1), date(2026, 8, 1), now=NOW)
    with pytest.raises(ExportError, match="開始日"):
        export_parquet(root, tmp_path / "exports", date(2026, 8, 2), date(2026, 8, 1), now=NOW)
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        _date("2026/08/01")

    _write(root, "sen66", "2026-08-01", _rows(datetime(2026, 8, 1, tzinfo=JST)))
    import data_exporter.engine as engine
    monkeypatch.setattr(engine, "_write_dataset_csv", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ExportError, match="失敗"):
        export_parquet(root, tmp_path / "exports", date(2026, 8, 1), date(2026, 8, 1), now=NOW)
    assert not list((tmp_path / "exports").glob("*.zip"))
    assert not list((tmp_path / "exports").glob("*.partial"))
