"""Read dashboard instantaneous values from sensor-collector latest JSON files."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from app.data.parquet_repository import LatestIchijoPowerFlow, LatestPower, LatestSen66

logger = logging.getLogger(__name__)


class LatestRepository:
    """Read the atomically replaced latest records produced by sensor-collector."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def latest_power(self) -> LatestPower | None:
        payload = self._payload_for("broute_power.json")
        if payload is None:
            return None
        try:
            return LatestPower(
                measured_at=_required_datetime(payload, "measured_at"),
                net_power_w=_required_number(payload, "net_power_w"),
            )
        except (TypeError, ValueError, KeyError) as error:
            logger.warning("Invalid latest B-route power data: %s", error)
            return None

    def latest_sen66(self) -> LatestSen66 | None:
        record = self._record_for("sen66.json")
        if record is None:
            return None
        try:
            payload = _required_payload(record)
            return LatestSen66(
                measured_at=_required_datetime(record, "received_at"),
                temperature_c=_required_number(payload, "temperature_celsius"),
                relative_humidity_pct=_required_number(payload, "relative_humidity_percent"),
                co2_ppm=_required_number(payload, "co2_ppm"),
                pm2_5_ug_m3=_required_number(payload, "pm2_5_ug_m3"),
                voc_index=_optional_number(payload, "voc_index"),
            )
        except (TypeError, ValueError, KeyError) as error:
            logger.warning("Invalid latest SEN66 data: %s", error)
            return None

    def latest_ichijo_power_flow(self) -> LatestIchijoPowerFlow | None:
        payload = self._payload_for("ichijo_power_flow.json")
        if payload is None:
            return None
        try:
            operating_state = payload["battery_operating_state"]
            if operating_state is not None and not isinstance(operating_state, str):
                raise TypeError("battery_operating_state must be a string or null")
            return LatestIchijoPowerFlow(
                measured_at=_required_datetime(payload, "measured_at"),
                load_power_w=_required_number(payload, "load_power_w"),
                pv_power_w=_required_number(payload, "pv_power_w"),
                grid_import_power_w=_required_number(payload, "grid_import_power_w"),
                grid_export_power_w=_required_number(payload, "grid_export_power_w"),
                battery_soc_percent=_required_number(payload, "battery_soc_percent"),
                battery_charge_power_w=_required_number(payload, "battery_charge_power_w"),
                battery_discharge_power_w=_required_number(payload, "battery_discharge_power_w"),
                battery_operating_state=operating_state,
            )
        except (TypeError, ValueError, KeyError) as error:
            logger.warning("Invalid latest Ichijo power-flow data: %s", error)
            return None

    def _payload_for(self, filename: str) -> dict[str, Any] | None:
        record = self._record_for(filename)
        if record is None:
            return None
        try:
            return _required_payload(record)
        except (TypeError, KeyError) as error:
            logger.warning("Invalid latest record %s: %s", filename, error)
            return None

    def _record_for(self, filename: str) -> dict[str, Any] | None:
        path = self.data_root / filename
        try:
            with path.open(encoding="utf-8") as source:
                record = json.load(source)
        except FileNotFoundError:
            logger.warning("Latest cache file is unavailable: %s", path)
            return None
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Unable to read latest cache file %s: %s", path, error)
            return None
        if not isinstance(record, dict):
            logger.warning("Latest cache file is not a JSON object: %s", path)
            return None
        return record


def _required_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record["payload"]
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    return payload


def _required_number(payload: dict[str, Any], field: str) -> float:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    return float(value)


def _optional_number(payload: dict[str, Any], field: str) -> float | None:
    value = payload[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number or null")
    return float(value)


def _required_datetime(values: dict[str, Any], field: str) -> datetime:
    value = values[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an ISO 8601 string")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp
