"""CSV保存処理の単体テスト。"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from broute_meter.models import (
    CumulativeEnergyReading,
    InstantaneousPowerReading,
    IntervalEnergyReading,
)
from broute_meter.storage import CsvMeasurementStorage, StorageError

JST = timezone(timedelta(hours=9))


def _cumulative(
    metered_at: datetime,
    *,
    reverse: bool = True,
) -> CumulativeEnergyReading:
    return CumulativeEnergyReading(
        metered_at=metered_at,
        received_at=metered_at.replace(minute=metered_at.minute + 1),
        forward_raw=1_234_510,
        reverse_raw=23_450 if reverse else None,
        forward_total_kwh=Decimal("12345.10"),
        reverse_total_kwh=Decimal("234.50") if reverse else None,
    )


def test_instantaneous_creates_daily_files_without_duplicate_headers(
    tmp_path: Path,
) -> None:
    storage = CsvMeasurementStorage(tmp_path)
    storage.save_instantaneous(
        InstantaneousPowerReading(
            datetime(2026, 7, 24, 15, 0, tzinfo=JST),
            1250,
        )
    )
    storage.save_instantaneous(
        InstantaneousPowerReading(
            datetime(2026, 7, 24, 15, 0, 10, tzinfo=JST),
            -840,
        )
    )
    storage.save_instantaneous(
        InstantaneousPowerReading(
            datetime(2026, 7, 25, 0, 0, tzinfo=JST),
            0,
        )
    )

    first = (tmp_path / "instantaneous_power_20260724.csv").read_text(
        encoding="utf-8"
    )
    second = (tmp_path / "instantaneous_power_20260725.csv").read_text(
        encoding="utf-8"
    )
    assert first.splitlines() == [
        "measured_at,net_power_w",
        "2026-07-24T15:00:00+09:00,1250",
        "2026-07-24T15:00:10+09:00,-840",
    ]
    assert second.count("measured_at,net_power_w") == 1


def test_cumulative_deduplicates_across_restart_and_switches_month(
    tmp_path: Path,
) -> None:
    july = _cumulative(datetime(2026, 7, 31, 23, 30, tzinfo=JST))
    august = _cumulative(
        datetime(2026, 8, 1, 0, 0, tzinfo=JST),
        reverse=False,
    )

    first_storage = CsvMeasurementStorage(tmp_path)
    assert first_storage.save_cumulative(july)
    assert not first_storage.save_cumulative(july)
    first_storage.close()

    restarted_storage = CsvMeasurementStorage(tmp_path)
    assert not restarted_storage.save_cumulative(july)
    assert restarted_storage.save_cumulative(august)

    with (tmp_path / "cumulative_energy_202607.csv").open(
        encoding="utf-8",
        newline="",
    ) as stream:
        july_rows = list(csv.DictReader(stream))
    with (tmp_path / "cumulative_energy_202608.csv").open(
        encoding="utf-8",
        newline="",
    ) as stream:
        august_rows = list(csv.DictReader(stream))

    assert len(july_rows) == 1
    assert august_rows[0]["reverse_raw"] == ""
    assert august_rows[0]["reverse_total_kwh"] == ""
    assert restarted_storage.latest_cumulative() == august


def test_interval_csv_deduplicates_end_time_and_preserves_quality(
    tmp_path: Path,
) -> None:
    storage = CsvMeasurementStorage(tmp_path)
    reading = IntervalEnergyReading(
        start_at=datetime(2026, 7, 24, 14, 30, tzinfo=JST),
        end_at=datetime(2026, 7, 24, 15, 0, tzinfo=JST),
        import_energy_kwh=Decimal("0.35"),
        export_energy_kwh=None,
        quality_status="reverse_not_supported",
    )

    assert storage.save_interval(reading)
    assert not storage.save_interval(reading)
    assert not CsvMeasurementStorage(tmp_path).save_interval(reading)

    rows = (
        tmp_path / "interval_energy_202607.csv"
    ).read_text(encoding="utf-8").splitlines()
    assert rows == [
        "start_at,end_at,import_energy_kwh,export_energy_kwh,quality_status",
        (
            "2026-07-24T14:30:00+09:00,2026-07-24T15:00:00+09:00,"
            "0.35,,reverse_not_supported"
        ),
    ]


def test_latest_cumulative_is_none_without_files(tmp_path: Path) -> None:
    assert CsvMeasurementStorage(tmp_path).latest_cumulative() is None


def test_reject_existing_cumulative_file_with_wrong_header(tmp_path: Path) -> None:
    (tmp_path / "cumulative_energy_202607.csv").write_text(
        "wrong,header\n",
        encoding="utf-8",
    )
    storage = CsvMeasurementStorage(tmp_path)

    with pytest.raises(StorageError, match="ヘッダー"):
        storage.save_cumulative(
            _cumulative(datetime(2026, 7, 24, 15, 0, tzinfo=JST))
        )


def test_wrap_write_failure_as_storage_error(tmp_path: Path) -> None:
    file_instead_of_directory = tmp_path / "data"
    file_instead_of_directory.write_text("not a directory", encoding="utf-8")
    storage = CsvMeasurementStorage(file_instead_of_directory)

    with pytest.raises(StorageError):
        storage.save_instantaneous(
            InstantaneousPowerReading(
                datetime(2026, 7, 24, 15, 0, tzinfo=JST),
                1,
            )
        )
