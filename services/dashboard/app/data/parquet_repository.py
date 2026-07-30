"""Read dashboard measurements from the processed Parquet datasets."""

from dataclasses import dataclass
from datetime import date, datetime
import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestPower:
    measured_at: datetime
    net_power_w: float


@dataclass(frozen=True)
class LatestSen66:
    measured_at: datetime
    temperature_c: float | None
    relative_humidity_pct: float | None
    co2_ppm: float | None
    pm2_5_ug_m3: float | None


@dataclass(frozen=True)
class EnergyTotals:
    import_energy_kwh: float
    export_energy_kwh: float


class ParquetRepository:
    """Read-only repository over date-partitioned processed Parquet files."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def latest_power(self) -> LatestPower | None:
        row = self._one(
            "broute_power",
            """
            SELECT measured_at, net_power_w
            FROM read_parquet(?, hive_partitioning = true)
            ORDER BY measured_at DESC
            LIMIT 1
            """,
        )
        if row is None:
            return None
        return LatestPower(measured_at=row[0], net_power_w=float(row[1]))

    def latest_sen66(self) -> LatestSen66 | None:
        row = self._one(
            "sen66",
            """
            SELECT measured_at, temperature_c, relative_humidity_pct, co2_ppm, pm2_5_ug_m3
            FROM read_parquet(?, hive_partitioning = true)
            ORDER BY measured_at DESC
            LIMIT 1
            """,
        )
        if row is None:
            return None
        return LatestSen66(
            measured_at=row[0],
            temperature_c=_optional_float(row[1]),
            relative_humidity_pct=_optional_float(row[2]),
            co2_ppm=_optional_float(row[3]),
            pm2_5_ug_m3=_optional_float(row[4]),
        )

    def today_energy_totals(self, today: date) -> EnergyTotals:
        row = self._one(
            "broute_interval_energy",
            """
            SELECT
                COALESCE(SUM(import_energy_kwh), 0),
                COALESCE(SUM(export_energy_kwh), 0)
            FROM read_parquet(?, hive_partitioning = true)
            WHERE CAST(end_at AT TIME ZONE 'Asia/Tokyo' AS DATE) = ?
            """,
            [today],
        )
        if row is None:
            return EnergyTotals(0.0, 0.0)
        return EnergyTotals(float(row[0]), float(row[1]))

    def _one(self, dataset: str, query: str, parameters: list[object] | None = None) -> tuple | None:
        parquet_glob = self.data_root / dataset / "**" / "*.parquet"
        if not any((self.data_root / dataset).glob("**/*.parquet")):
            return None

        connection = duckdb.connect(":memory:")
        try:
            return connection.execute(query, [str(parquet_glob), *(parameters or [])]).fetchone()
        except duckdb.Error:
            logger.exception("Unable to read dashboard dataset: %s", dataset)
            return None
        finally:
            connection.close()


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)
