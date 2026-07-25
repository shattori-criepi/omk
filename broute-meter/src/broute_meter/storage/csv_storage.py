"""日次・月次CSVによる計測値保存。"""

from __future__ import annotations

import csv
import os
import threading
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from broute_meter.models import (
    CumulativeEnergyReading,
    InstantaneousPowerReading,
    IntervalEnergyReading,
)
from broute_meter.storage.base import StorageError

_INSTANTANEOUS_HEADER = ("measured_at", "net_power_w")
_CUMULATIVE_HEADER = (
    "metered_at",
    "received_at",
    "forward_raw",
    "reverse_raw",
    "forward_total_kwh",
    "reverse_total_kwh",
)
_INTERVAL_HEADER = (
    "start_at",
    "end_at",
    "import_energy_kwh",
    "export_energy_kwh",
    "quality_status",
)


class CsvMeasurementStorage:
    """計測値をUTF-8 CSVへ追記し、積算計量時刻の重複を防ぐ。"""

    def __init__(self, data_directory: Path) -> None:
        self._directory = Path(data_directory)
        self._lock = threading.RLock()
        self._known_cumulative_times: dict[Path, set[str]] = {}
        self._known_interval_ends: dict[Path, set[str]] = {}
        self._validated_headers: set[Path] = set()

    def save_instantaneous(self, reading: InstantaneousPowerReading) -> None:
        """瞬時電力を計測日のファイルへ追記する。"""

        path = self._directory / (
            f"instantaneous_power_{reading.measured_at:%Y%m%d}.csv"
        )
        self._append_row(
            path,
            _INSTANTANEOUS_HEADER,
            (reading.measured_at.isoformat(), reading.net_power_w),
        )

    def save_cumulative(self, reading: CumulativeEnergyReading) -> bool:
        """積算値を計量月のファイルへ、同一計量時刻は一度だけ保存する。"""

        path = self._directory / f"cumulative_energy_{reading.metered_at:%Y%m}.csv"
        key = reading.metered_at.isoformat()
        with self._lock:
            known = self._known_cumulative_times.get(path)
            if known is None:
                known = self._read_existing_cumulative_times(path)
                self._known_cumulative_times[path] = known
            if key in known:
                return False

            self._append_row_unlocked(
                path,
                _CUMULATIVE_HEADER,
                (
                    key,
                    reading.received_at.isoformat(),
                    reading.forward_raw,
                    "" if reading.reverse_raw is None else reading.reverse_raw,
                    str(reading.forward_total_kwh),
                    (
                        ""
                        if reading.reverse_total_kwh is None
                        else str(reading.reverse_total_kwh)
                    ),
                ),
            )
            known.add(key)
            return True

    def latest_cumulative(self) -> CumulativeEnergyReading | None:
        """全月次CSVから計量時刻が最新の積算値を復元する。"""

        with self._lock:
            latest: CumulativeEnergyReading | None = None
            try:
                paths = sorted(self._directory.glob("cumulative_energy_*.csv"))
                for path in paths:
                    with path.open(encoding="utf-8", newline="") as stream:
                        reader = csv.DictReader(stream)
                        if reader.fieldnames != list(_CUMULATIVE_HEADER):
                            raise StorageError(
                                f"積算CSVのヘッダーが不正です: {path}"
                            )
                        for row in reader:
                            candidate = _cumulative_from_row(row)
                            if (
                                latest is None
                                or candidate.metered_at > latest.metered_at
                            ):
                                latest = candidate
                return latest
            except (OSError, UnicodeError, csv.Error, KeyError, ValueError) as exc:
                raise StorageError("保存済み積算CSVを復元できませんでした。") from exc

    def save_interval(self, reading: IntervalEnergyReading) -> bool:
        """30分値を終了月のCSVへ、同一終了時刻は一度だけ保存する。"""

        path = self._directory / f"interval_energy_{reading.end_at:%Y%m}.csv"
        key = reading.end_at.isoformat()
        with self._lock:
            known = self._known_interval_ends.get(path)
            if known is None:
                known = self._read_existing_keys(path, _INTERVAL_HEADER, "end_at")
                self._known_interval_ends[path] = known
            if key in known:
                return False

            self._append_row_unlocked(
                path,
                _INTERVAL_HEADER,
                (
                    reading.start_at.isoformat(),
                    key,
                    str(reading.import_energy_kwh),
                    (
                        ""
                        if reading.export_energy_kwh is None
                        else str(reading.export_energy_kwh)
                    ),
                    reading.quality_status,
                ),
            )
            known.add(key)
            return True

    def close(self) -> None:
        """各書込みでflush済みのため、追加の処理は不要。"""

    def _append_row(
        self,
        path: Path,
        header: Iterable[object],
        row: Iterable[object],
    ) -> None:
        with self._lock:
            self._append_row_unlocked(path, header, row)

    def _append_row_unlocked(
        self,
        path: Path,
        header: Iterable[object],
        row: Iterable[object],
    ) -> None:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            needs_header = not path.exists() or path.stat().st_size == 0
            expected_header = [str(item) for item in header]
            if not needs_header and path not in self._validated_headers:
                with path.open(encoding="utf-8", newline="") as existing:
                    actual_header = next(csv.reader(existing), None)
                if actual_header != expected_header:
                    raise StorageError(f"CSVのヘッダーが不正です: {path}")
                self._validated_headers.add(path)
            with path.open("a", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                if needs_header:
                    writer.writerow(expected_header)
                writer.writerow(row)
                stream.flush()
                os.fsync(stream.fileno())
            self._validated_headers.add(path)
        except StorageError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise StorageError(f"CSVへ書き込めませんでした: {path}") from exc

    def _read_existing_cumulative_times(self, path: Path) -> set[str]:
        return self._read_existing_keys(path, _CUMULATIVE_HEADER, "metered_at")

    def _read_existing_keys(
        self,
        path: Path,
        header: tuple[str, ...],
        key_name: str,
    ) -> set[str]:
        if not path.exists():
            return set()
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != list(header):
                    raise StorageError(f"CSVのヘッダーが不正です: {path}")
                return {row[key_name] for row in reader if row.get(key_name)}
        except StorageError:
            raise
        except (OSError, UnicodeError, csv.Error, KeyError) as exc:
            raise StorageError(f"CSVを読み取れませんでした: {path}") from exc


def _cumulative_from_row(row: dict[str, str]) -> CumulativeEnergyReading:
    reverse_raw = row["reverse_raw"]
    reverse_total = row["reverse_total_kwh"]
    return CumulativeEnergyReading(
        metered_at=datetime.fromisoformat(row["metered_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        forward_raw=int(row["forward_raw"]),
        reverse_raw=int(reverse_raw) if reverse_raw else None,
        forward_total_kwh=Decimal(row["forward_total_kwh"]),
        reverse_total_kwh=Decimal(reverse_total) if reverse_total else None,
    )
