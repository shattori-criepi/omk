"""Translate confirmed Ichijo ECHONET values into OMK's power-flow contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

STATE_CODES = {0x42: "charging", 0x43: "discharging", 0x44: "standby"}


def unsigned_1(edt: bytes) -> int:
    if len(edt) != 1:
        raise ValueError("expected unsigned 1-byte EDT")
    return edt[0]


def unsigned_2(edt: bytes) -> int:
    if len(edt) != 2:
        raise ValueError("expected unsigned 2-byte EDT")
    return int.from_bytes(edt, "big", signed=False)


def signed_4(edt: bytes) -> int:
    if len(edt) != 4:
        raise ValueError("expected signed 4-byte EDT")
    return int.from_bytes(edt, "big", signed=True)


def battery_state(edt: bytes) -> str:
    return STATE_CODES.get(unsigned_1(edt), "unknown")


def normalize(raw: dict[str, Any], *, device_id: str, measured_at: datetime, errors: list[str]) -> dict[str, Any]:
    """Build an OMK message, preserving partial reads as JSON null."""
    pv = raw.get("pv_power_w")
    battery = raw.get("battery_raw_w")
    grid = raw.get("grid_raw_w")
    pcs = raw.get("pcs_raw_w")
    charge = max(battery, 0) if battery is not None else None
    discharge = max(-battery, 0) if battery is not None else None
    imported = max(grid, 0) if grid is not None else None
    exported = max(-grid, 0) if grid is not None else None
    # Confirmed equipment behavior: negative E7 is AC-side output. Positive E7 is unverified.
    output = max(-pcs, 0) if pcs is not None else None
    required = (output, imported, exported)
    load = None if any(value is None for value in required) else output + imported - exported
    quality = "degraded" if errors or (load is not None and load < 0) else "normal"
    if load is not None and load < 0:
        errors = [*errors, "negative_load_power"]
    return {
        "device_id": device_id,
        "measured_at": measured_at.isoformat(timespec="seconds"),
        "pv_power_w": pv,
        "battery_soc_percent": raw.get("battery_soc_percent"),
        "battery_charge_power_w": charge,
        "battery_discharge_power_w": discharge,
        "battery_operating_state": raw.get("battery_operating_state"),
        "battery_operating_state_raw": raw.get("battery_operating_state_raw"),
        "grid_import_power_w": imported,
        "grid_export_power_w": exported,
        "pcs_ac_output_power_w": output,
        "load_power_w": load,
        "quality": quality,
        "errors": errors,
    }
