"""Schema-aware, in-memory one-minute aggregation for Harvest Data."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

AVERAGES = {
    "sen66": {
        "temperature_celsius": "sen66_temperature_c",
        "relative_humidity_percent": "sen66_relative_humidity_percent",
        "co2_ppm": "sen66_co2_ppm",
        "pm1_0_ug_m3": "sen66_pm1_0_ug_m3",
        "pm2_5_ug_m3": "sen66_pm2_5_ug_m3",
        "pm4_0_ug_m3": "sen66_pm4_0_ug_m3",
        "pm10_0_ug_m3": "sen66_pm10_0_ug_m3",
        "voc_index": "sen66_voc_index",
        "nox_index": "sen66_nox_index",
    },
    "power": {"net_power_w": "broute_grid_power_w"},
    "power-flow": {
        "pv_power_w": "power_system_pv_power_w",
        "load_power_w": "power_system_load_power_w",
        "grid_import_power_w": "power_system_grid_import_power_w",
        "grid_export_power_w": "power_system_grid_export_power_w",
        "pcs_ac_output_power_w": "power_system_pcs_ac_output_power_w",
        "battery_charge_power_w": "power_system_battery_charge_power_w",
        "battery_discharge_power_w": "power_system_battery_discharge_power_w",
    },
}
LATEST = {
    "cumulative-energy": {
        "cumulative_energy_import_kwh": "broute_grid_import_energy_kwh",
        "cumulative_energy_export_kwh": "broute_grid_export_energy_kwh",
    },
    "power-flow": {"battery_soc_percent": "power_system_battery_soc_percent"},
}
ENVIRONMENT_FIELDS = (
    "temperature_c",
    "relative_humidity_percent",
    "co2_ppm",
)
SENSOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _minute(value: datetime) -> datetime:
    return value.astimezone(JST).replace(second=0, microsecond=0)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


class MinuteAggregator:
    """Keeps only the current open minute; callers discard it on restart."""

    def __init__(self) -> None:
        self._start: datetime | None = None
        self._sums: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._latest: dict[str, float | int] = {}
        self._latest_times: dict[str, datetime] = {}

    def ingest(self, topic: str, payload: dict[str, Any], received_at: datetime) -> dict[str, Any] | None:
        start = _minute(received_at)
        completed = self._complete_before(start)
        if self._start is None:
            self._start = start
        if start != self._start or not self._is_measurement_topic(topic):
            return completed
        parts = topic.split("/")
        kind = parts[2]
        averages = self._environment_averages(parts[1], payload) if kind == "environment" else AVERAGES.get(kind, {})
        for source, target in averages.items():
            value = _number(payload.get(source))
            if value is not None:
                self._sums[target] = self._sums.get(target, 0.0) + value
                self._counts[target] = self._counts.get(target, 0) + 1
        for source, target in LATEST.get(kind, {}).items():
            value = _number(payload.get(source))
            if value is not None and received_at >= self._latest_times.get(target, self._start):
                self._latest[target] = value
                self._latest_times[target] = received_at
        return completed

    def flush_due(self, now: datetime) -> dict[str, Any] | None:
        return self._complete_before(_minute(now))

    def _complete_before(self, next_start: datetime) -> dict[str, Any] | None:
        if self._start is None or self._start >= next_start:
            return None
        record: dict[str, Any] = {"time": self._start.isoformat(timespec="seconds")}
        for field, total in self._sums.items():
            record[field] = total / self._counts[field]
        record.update(self._latest)
        if "broute_grid_power_w" in record:
            power = record["broute_grid_power_w"]
            record["broute_grid_import_power_w"] = max(power, 0)
            record["broute_grid_export_power_w"] = max(-power, 0)
        self._start = None
        self._sums.clear()
        self._counts.clear()
        self._latest.clear()
        self._latest_times.clear()
        return record if len(record) > 1 else None

    @staticmethod
    def _is_measurement_topic(topic: str) -> bool:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "omk" or not parts[1]:
            return False
        if parts[2] == "environment":
            return SENSOR_ID_RE.fullmatch(parts[1]) is not None
        return parts[2] in (set(AVERAGES) | set(LATEST))

    @staticmethod
    def _environment_averages(sensor_id: str, payload: dict[str, Any]) -> dict[str, str]:
        """Map validated dynamic environment fields without altering fixed schemas."""
        return {
            field: f"{sensor_id}_{field}"
            for field in ENVIRONMENT_FIELDS
            if field in payload
        }
