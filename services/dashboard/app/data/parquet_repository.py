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
    voc_index: float | None


@dataclass(frozen=True)
class LatestIchijoPowerFlow:
    measured_at: datetime
    load_power_w: float
    pv_power_w: float
    grid_import_power_w: float
    grid_export_power_w: float
    battery_soc_percent: float
    battery_charge_power_w: float
    battery_discharge_power_w: float
    battery_operating_state: str | None


@dataclass(frozen=True)
class EnergyTotals:
    import_energy_kwh: float | None
    export_energy_kwh: float | None


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
            SELECT measured_at, temperature_c, relative_humidity_pct, co2_ppm, pm2_5_ug_m3, voc_index
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
            voc_index=_optional_float(row[5]),
        )

    def latest_ichijo_power_flow(self) -> LatestIchijoPowerFlow | None:
        row = self._one(
            "ichijo_power_flow",
            """
            SELECT
                measured_at,
                load_power_w,
                pv_power_w,
                grid_import_power_w,
                grid_export_power_w,
                battery_soc_percent,
                battery_charge_power_w,
                battery_discharge_power_w,
                battery_operating_state
            FROM read_parquet(?, hive_partitioning = true)
            ORDER BY measured_at DESC
            LIMIT 1
            """,
        )
        if row is None:
            return None

        return LatestIchijoPowerFlow(
            measured_at=row[0],
            load_power_w=float(row[1]),
            pv_power_w=float(row[2]),
            grid_import_power_w=float(row[3]),
            grid_export_power_w=float(row[4]),
            battery_soc_percent=float(row[5]),
            battery_charge_power_w=float(row[6]),
            battery_discharge_power_w=float(row[7]),
            battery_operating_state=row[8],
        )

    def today_energy_totals(self, today: date) -> EnergyTotals:
        row = self._one(
            "broute_interval_energy",
            """
            SELECT
                SUM(import_energy_kwh),
                SUM(export_energy_kwh)
            FROM read_parquet(?, hive_partitioning = true)
            WHERE CAST(end_at AT TIME ZONE 'Asia/Tokyo' AS DATE) = ?
            """,
            [today],
        )
        if row is None:
            return EnergyTotals(None, None)
        return EnergyTotals(_optional_float(row[0]), _optional_float(row[1]))

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
