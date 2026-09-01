"""Streaming JSONL normalisation and atomic, partitioned Parquet output."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

LOGGER = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")

POWER = "broute_power"
CUMULATIVE = "broute_cumulative_energy"
SEN66 = "sen66"
INTERVAL = "broute_interval_energy"
ICHIJO_POWER_FLOW = "ichijo_power_flow"
BLE_ENVIRONMENT = "ble_environment"
BLE_MOTION = "ble_motion"
BLE_CONTACT = "ble_contact"
BLE_POWER = "ble_power"

SCHEMAS = {
    POWER: pa.schema([
        ("device_id", pa.string()), ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("net_power_w", pa.int64()), ("topic", pa.string()), ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    CUMULATIVE: pa.schema([
        ("device_id", pa.string()), ("metered_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("cumulative_energy_import_kwh", pa.float64()), ("cumulative_energy_export_kwh", pa.float64()),
        ("topic", pa.string()), ("source_file", pa.string()), ("source_line_number", pa.int64()),
    ]),
    SEN66: pa.schema([
        ("device_id", pa.string()), ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("temperature_c", pa.float64()), ("relative_humidity_pct", pa.float64()), ("co2_ppm", pa.float64()),
        ("pm1_0_ug_m3", pa.float64()), ("pm2_5_ug_m3", pa.float64()), ("pm4_0_ug_m3", pa.float64()),
        ("pm10_0_ug_m3", pa.float64()), ("voc_index", pa.float64()), ("nox_index", pa.float64()),
        ("topic", pa.string()), ("source_file", pa.string()), ("source_line_number", pa.int64()),
    ]),
    INTERVAL: pa.schema([
        ("device_id", pa.string()), ("start_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("end_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("import_energy_kwh", pa.float64()), ("export_energy_kwh", pa.float64()),
        ("quality_status", pa.string()), ("topic", pa.string()), ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    ICHIJO_POWER_FLOW: pa.schema([
        ("device_id", pa.string()),
        ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("pv_power_w", pa.int64()),
        ("load_power_w", pa.int64()),
        ("grid_import_power_w", pa.int64()),
        ("grid_export_power_w", pa.int64()),
        ("battery_soc_percent", pa.float64()),
        ("battery_charge_power_w", pa.int64()),
        ("battery_discharge_power_w", pa.int64()),
        ("battery_operating_state", pa.string()),
        ("battery_operating_state_raw", pa.int64()),
        ("pcs_ac_output_power_w", pa.int64()),
        ("quality", pa.string()),
        ("errors", pa.list_(pa.string())),
        ("topic", pa.string()),
        ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    BLE_ENVIRONMENT: pa.schema([
        ("device_id", pa.string()),
        ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("temperature_c", pa.float64()),
        ("relative_humidity_pct", pa.float64()),
        ("co2_ppm", pa.float64()),
        ("quality", pa.string()),
        ("source", pa.string()),
        ("relay_node_id", pa.string()),
        ("topic", pa.string()),
        ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    BLE_MOTION: pa.schema([
        ("device_id", pa.string()),
        ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("motion_state", pa.int64()),
        ("battery_percent", pa.int64()),
        ("light_level", pa.int64()),
        ("topic", pa.string()),
        ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    BLE_CONTACT: pa.schema([
        ("device_id", pa.string()),
        ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("contact_state", pa.int64()),
        ("topic", pa.string()),
        ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
    BLE_POWER: pa.schema([
        ("device_id", pa.string()),
        ("measured_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("collector_received_at", pa.timestamp("us", tz="Asia/Tokyo")),
        ("power_w", pa.float64()),
        ("switch_state", pa.int64()),
        ("quality", pa.string()),
        ("topic", pa.string()),
        ("source_file", pa.string()),
        ("source_line_number", pa.int64()),
    ]),
}


class RecordError(ValueError):
    """A record validation failure whose category is safe to report."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class TransformResult:
    input_file: Path
    total_lines: int = 0
    written: Counter[str] = field(default_factory=Counter)
    ignored_topics: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    outputs: list[Path] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def converted(self) -> int:
        return sum(self.written.values())

    @property
    def ignored(self) -> int:
        return sum(self.ignored_topics.values())

    @property
    def skipped(self) -> int:
        return self.total_lines - self.converted


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RecordError("missing_required_field", f"{field_name} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecordError("invalid_datetime", f"invalid {field_name}: {value!r}") from error
    if parsed.tzinfo is None:
        raise RecordError("invalid_datetime", f"{field_name} must include a timezone")
    return parsed.astimezone(JST)


def _number(payload: dict[str, Any], field_name: str, *, required: bool = False, integer: bool = False) -> float | int | None:
    value = payload.get(field_name)
    if value is None:
        if required:
            raise RecordError("missing_required_field", f"missing {field_name}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RecordError("invalid_number", f"invalid numeric {field_name}")
    if integer and isinstance(value, float) and not value.is_integer():
        raise RecordError("invalid_number", f"{field_name} must be an integer")
    return int(value) if integer else float(value)


def _payload(record: dict[str, Any]) -> tuple[str, dict[str, Any], datetime]:
    topic = record.get("topic")
    payload = record.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        raise RecordError("missing_required_field", "topic and object payload are required")
    collector_received_at = _timestamp(record.get("received_at"), "collector received_at")
    if not isinstance(payload.get("device_id"), str) or not payload["device_id"]:
        raise RecordError("missing_required_field", "payload.device_id is required")
    return topic, payload, collector_received_at


def _common(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, dict[str, Any], datetime, dict[str, Any]]:
    topic, payload, collector_received_at = _payload(record)
    return topic, payload, collector_received_at, {
        "device_id": payload["device_id"], "collector_received_at": collector_received_at,
        "topic": topic, "source_file": str(source_file), "source_line_number": line_number,
    }


def _ble_common(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, dict[str, Any], datetime, dict[str, Any]]:
    topic = record.get("topic")
    payload = record.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        raise RecordError("missing_required_field", "topic and object payload are required")
    collector_received_at = _timestamp(record.get("received_at"), "collector received_at")
    if "device_id" in payload:
        device_id = payload["device_id"]
        if not isinstance(device_id, str) or not device_id:
            raise RecordError("missing_required_field", "payload.device_id must be a non-empty string")
    else:
        sensor_id = payload.get("sensor_id")
        if not isinstance(sensor_id, str) or not sensor_id:
            raise RecordError("missing_required_field", "payload.device_id or payload.sensor_id is required")
        device_id = sensor_id
    return topic, payload, collector_received_at, {
        "device_id": device_id, "collector_received_at": collector_received_at,
        "topic": topic, "source_file": str(source_file), "source_line_number": line_number,
    }


def _ble_measured_at(payload: dict[str, Any], collector_received_at: datetime) -> datetime:
    if "measured_at" not in payload:
        return collector_received_at
    return _timestamp(payload["measured_at"], "payload.measured_at")


def _power(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    topic, payload, _, row = _common(record, source_file, line_number)
    row["measured_at"] = _timestamp(payload.get("measured_at"), "payload.measured_at")
    row["received_at"] = _timestamp(payload["received_at"], "payload.received_at") if "received_at" in payload else None
    row["net_power_w"] = _number(payload, "net_power_w", required=True, integer=True)
    return POWER, row["measured_at"].date().isoformat(), row


def _cumulative(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    topic, payload, _, row = _common(record, source_file, line_number)
    row["metered_at"] = _timestamp(payload.get("metered_at"), "payload.metered_at")
    row["received_at"] = _timestamp(payload.get("received_at"), "payload.received_at")
    row["cumulative_energy_import_kwh"] = _number(payload, "cumulative_energy_import_kwh")
    row["cumulative_energy_export_kwh"] = _number(payload, "cumulative_energy_export_kwh")
    if row["cumulative_energy_import_kwh"] is None and row["cumulative_energy_export_kwh"] is None:
        raise RecordError("missing_required_field", "at least one cumulative energy value is required")
    return CUMULATIVE, row["metered_at"].date().isoformat(), row


def _sen66(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    topic, payload, collector_received_at, row = _common(record, source_file, line_number)
    # ESP32 SEN66 firmware has no clock. Its collector receipt time is its measurement time.
    row["measured_at"] = _timestamp(payload["measured_at"], "payload.measured_at") if "measured_at" in payload else collector_received_at
    row["received_at"] = _timestamp(payload["received_at"], "payload.received_at") if "received_at" in payload else None
    mappings = {"temperature_c": "temperature_celsius", "relative_humidity_pct": "relative_humidity_percent",
                "co2_ppm": "co2_ppm", "pm1_0_ug_m3": "pm1_0_ug_m3", "pm2_5_ug_m3": "pm2_5_ug_m3",
                "pm4_0_ug_m3": "pm4_0_ug_m3", "pm10_0_ug_m3": "pm10_0_ug_m3", "voc_index": "voc_index", "nox_index": "nox_index"}
    for output_name, input_name in mappings.items():
        row[output_name] = _number(payload, input_name)
    return SEN66, row["measured_at"].date().isoformat(), row


def _interval(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    _, payload, _, row = _common(record, source_file, line_number)
    row["start_at"] = _timestamp(payload.get("start_at"), "payload.start_at")
    row["end_at"] = _timestamp(payload.get("end_at"), "payload.end_at")
    if row["start_at"] > row["end_at"]:
        raise RecordError("invalid_datetime", "payload.start_at must not be after payload.end_at")
    row["import_energy_kwh"] = _number(payload, "import_energy_kwh", required=True)
    row["export_energy_kwh"] = _number(payload, "export_energy_kwh", required=True)
    quality_status = payload.get("quality_status")
    if not isinstance(quality_status, str) or not quality_status:
        raise RecordError("missing_required_field", "payload.quality_status is required")
    row["quality_status"] = quality_status
    return INTERVAL, row["end_at"].date().isoformat(), row


def _ichijo_power_flow(
    record: dict[str, Any],
    source_file: Path,
    line_number: int,
) -> tuple[str, str, dict[str, Any]]:
    _, payload, _, row = _common(record, source_file, line_number)

    row["measured_at"] = _timestamp(
        payload.get("measured_at"),
        "payload.measured_at",
    )

    quality = _optional_string(payload, "quality")
    is_degraded = quality == "degraded"

    integer_fields = (
        "pv_power_w",
        "load_power_w",
        "grid_import_power_w",
        "grid_export_power_w",
        "battery_charge_power_w",
        "battery_discharge_power_w",
        "pcs_ac_output_power_w",
    )
    for field_name in integer_fields:
        row[field_name] = _number(
            payload,
            field_name,
            required=not is_degraded,
            integer=True,
        )

    row["battery_soc_percent"] = _number(
        payload,
        "battery_soc_percent",
        required=not is_degraded,
    )
    row["battery_operating_state_raw"] = _number(
        payload,
        "battery_operating_state_raw",
        integer=True,
    )

    row["battery_operating_state"] = _optional_string(
        payload,
        "battery_operating_state",
    )
    row["quality"] = quality
    row["errors"] = _optional_string_list(payload, "errors")

    return (
        ICHIJO_POWER_FLOW,
        row["measured_at"].date().isoformat(),
        row,
    )


def _optional_string(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecordError("invalid_string", f"{field_name} must be a string")
    return value


def _optional_string_list(payload: dict[str, Any], field_name: str) -> list[str] | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordError("invalid_type", f"{field_name} must be a list of strings")
    return value


def _ble_environment(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    _, payload, collector_received_at, row = _ble_common(record, source_file, line_number)
    row["measured_at"] = _ble_measured_at(payload, collector_received_at)
    row["temperature_c"] = _number(payload, "temperature_c")
    row["relative_humidity_pct"] = _number(payload, "relative_humidity_percent")
    row["co2_ppm"] = _number(payload, "co2_ppm")
    row["quality"] = _optional_string(payload, "quality")
    row["source"] = _optional_string(payload, "source")
    row["relay_node_id"] = _optional_string(payload, "relay_node_id")
    return BLE_ENVIRONMENT, row["measured_at"].date().isoformat(), row


def _ble_motion(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    _, payload, collector_received_at, row = _ble_common(record, source_file, line_number)
    row["measured_at"] = _ble_measured_at(payload, collector_received_at)
    row["motion_state"] = _number(payload, "motion_state", integer=True)
    row["battery_percent"] = _number(payload, "battery_percent", integer=True)
    row["light_level"] = _number(payload, "light_level", integer=True)
    return BLE_MOTION, row["measured_at"].date().isoformat(), row


def _ble_contact(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    _, payload, collector_received_at, row = _ble_common(record, source_file, line_number)
    row["measured_at"] = _ble_measured_at(payload, collector_received_at)
    row["contact_state"] = _number(payload, "contact_state", integer=True)
    return BLE_CONTACT, row["measured_at"].date().isoformat(), row


def _ble_power(record: dict[str, Any], source_file: Path, line_number: int) -> tuple[str, str, dict[str, Any]]:
    _, payload, collector_received_at, row = _ble_common(record, source_file, line_number)
    row["measured_at"] = _ble_measured_at(payload, collector_received_at)
    row["power_w"] = _number(payload, "power_w")
    row["switch_state"] = _number(payload, "switch_state", integer=True)
    row["quality"] = _optional_string(payload, "quality")
    return BLE_POWER, row["measured_at"].date().isoformat(), row



NORMALIZERS: dict[str, Callable[[dict[str, Any], Path, int], tuple[str, str, dict[str, Any]]]] = {
    "cumulative-energy": _cumulative,
    "interval-energy": _interval,
    "sen66": _sen66,
    "power-flow": _ichijo_power_flow,
    "environment": _ble_environment,
    "motion": _ble_motion,
    "contact": _ble_contact,
}


def _normalizer_for(record: dict[str, Any]) -> Callable[[dict[str, Any], Path, int], tuple[str, str, dict[str, Any]]]:
    topic = record.get("topic")
    if not isinstance(topic, str):
        raise RecordError("missing_required_field", "topic is required")
    suffix = topic.rsplit("/", 1)[-1]
    if not topic.startswith("omk/"):
        raise RecordError("unsupported_topic", f"unsupported MQTT topic: {topic}")
    if suffix == "power":
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise RecordError("missing_required_field", "topic and object payload are required")
        if "net_power_w" in payload:
            return _power
        if "power_w" in payload:
            return _ble_power
        raise RecordError("unsupported_topic", f"cannot identify power payload schema for MQTT topic: {topic}")
    if suffix not in NORMALIZERS:
        raise RecordError("unsupported_topic", f"unsupported MQTT topic: {topic}")
    return NORMALIZERS[suffix]


def _is_status_topic(record: dict[str, Any]) -> bool:
    topic = record.get("topic")
    if not isinstance(topic, str):
        return False
    parts = topic.split("/")
    if parts[0] != "omk":
        return False
    return (
        (len(parts) == 3 and bool(parts[1]) and parts[2] == "status")
        or (len(parts) == 4 and parts[1] == "node" and bool(parts[2]) and parts[3] == "status")
        or (len(parts) == 5 and parts[1] == "node" and bool(parts[2]) and parts[3] == "registration" and parts[4] == "status")
        or (len(parts) == 5 and parts[1] == "node" and bool(parts[2]) and parts[3] == "registration" and parts[4] in {"ack", "config"})
    )


def _error_file_name(input_file: Path) -> str:
    month_directory = input_file.parent
    year_directory = month_directory.parent
    if (
        len(year_directory.name) == 4
        and year_directory.name.isdigit()
        and len(month_directory.name) == 2
        and month_directory.name.isdigit()
        and len(input_file.stem) == 2
        and input_file.stem.isdigit()
    ):
        return f"{year_directory.name}-{month_directory.name}-{input_file.stem}.jsonl"
    return f"{input_file.stem}.jsonl"


def transform(input_file: Path, output_root: Path, *, dry_run: bool = False, error_root: Path | None = None) -> TransformResult:
    """Convert one JSONL file with source-file-level idempotent partition merges."""
    started = time.monotonic()
    result = TransformResult(input_file=input_file)
    writers: dict[tuple[str, str], tuple[Path, Path, pq.ParquetWriter]] = {}
    error_file = None if dry_run else (error_root or output_root.parent / "errors" / "transform") / _error_file_name(input_file)
    error_handle = None

    def report_error(category: str, line_number: int, raw: str, detail: str) -> None:
        result.errors[category] += 1
        LOGGER.debug("Skipping %s:%s category=%s detail=%s", input_file, line_number, category, detail)
        if error_file is not None:
            nonlocal error_handle
            if error_handle is None:
                error_file.parent.mkdir(parents=True, exist_ok=True)
                error_handle = error_file.open("w", encoding="utf-8")
            json.dump({"source_file": str(input_file), "source_line_number": line_number, "error": category,
                       "detail": detail, "raw_record": raw.rstrip("\n")}, error_handle, ensure_ascii=False, separators=(",", ":"))
            error_handle.write("\n")

    try:
        with input_file.open(encoding="utf-8") as source:
            for line_number, raw in enumerate(source, 1):
                result.total_lines += 1
                try:
                    record = json.loads(raw)
                    if not isinstance(record, dict):
                        raise RecordError("invalid_json", "record must be a JSON object")
                    if _is_status_topic(record):
                        result.ignored_topics[record["topic"]] += 1
                        LOGGER.debug("Ignoring management topic %s:%s topic=%s", input_file, line_number, record["topic"])
                        continue
                    dataset, date, row = _normalizer_for(record)(record, input_file, line_number)
                    result.written[dataset] += 1
                    if dry_run:
                        continue
                    key = (dataset, date)
                    if key not in writers:
                        destination = output_root / dataset / f"date={date}" / "data.parquet"
                        temporary = destination.with_name("data.parquet.incoming.tmp")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        writers[key] = (destination, temporary, pq.ParquetWriter(temporary, SCHEMAS[dataset], compression="zstd"))
                    writers[key][2].write_table(pa.Table.from_pylist([row], schema=SCHEMAS[dataset]))
                except json.JSONDecodeError as error:
                    report_error("invalid_json", line_number, raw, str(error))
                except RecordError as error:
                    report_error(error.category, line_number, raw, str(error))
        for _, _, writer in writers.values():
            writer.close()
        merged_outputs: list[tuple[Path, Path]] = []
        for (dataset, _), (destination, incoming, _) in writers.items():
            existing_rows = pq.ParquetFile(destination).read().to_pylist() if destination.exists() else []
            replacement_rows = [
                row for row in existing_rows if row.get("source_file") != str(input_file)
            ]
            replacement_rows.extend(pq.ParquetFile(incoming).read().to_pylist())
            temporary = destination.with_name("data.parquet.tmp")
            pq.write_table(
                pa.Table.from_pylist(replacement_rows, schema=SCHEMAS[dataset]),
                temporary,
                compression="zstd",
            )
            incoming.unlink()
            merged_outputs.append((destination, temporary))
        for destination, temporary in merged_outputs:
            os.replace(temporary, destination)
            result.outputs.append(destination)
    except Exception:
        for _, temporary, writer in writers.values():
            writer.close()
            temporary.unlink(missing_ok=True)
            temporary.with_name("data.parquet.tmp").unlink(missing_ok=True)
        raise
    finally:
        if error_handle is not None:
            error_handle.close()
        result.elapsed_seconds = time.monotonic() - started
    LOGGER.info("Transform complete input_file=%s records_read=%s records_written=%s dataset_counts=%s records_skipped=%s records_ignored=%s ignored_topics=%s errors=%s unsupported_topics=%s outputs=%s elapsed_seconds=%.3f",
                input_file, result.total_lines, result.converted, dict(result.written), result.skipped, result.ignored,
                dict(result.ignored_topics), sum(result.errors.values()), result.errors["unsupported_topic"], result.outputs, result.elapsed_seconds)
    return result
