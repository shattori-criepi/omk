"""Sequential read-only collection of the six confirmed properties."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from .normalizer import battery_state, normalize, signed_4, unsigned_1, unsigned_2

LOGGER = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


class PropertyReader(Protocol):
    def get(self, deoj: bytes, epc: int) -> bytes: ...


PROPERTIES = (
    ("pv_power", bytes.fromhex("027901"), 0xE0, "pv_power_w", unsigned_2),
    ("battery_soc", bytes.fromhex("027d01"), 0xE4, "battery_soc_percent", unsigned_1),
    ("battery_power", bytes.fromhex("027d01"), 0xD3, "battery_raw_w", signed_4),
    ("battery_state", bytes.fromhex("027d01"), 0xCF, "battery_operating_state", battery_state),
    ("grid_power", bytes.fromhex("028701"), 0xC6, "grid_raw_w", signed_4),
    ("pcs_power", bytes.fromhex("02a501"), 0xE7, "pcs_raw_w", signed_4),
)


class Collector:
    def __init__(self, reader: PropertyReader, *, device_id: str, property_interval_seconds: float,
                 now: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._reader = reader
        self._device_id = device_id
        self._property_interval = property_interval_seconds
        self._now = now or (lambda: datetime.now(JST))
        self._sleep = sleep

    def collect(self) -> dict[str, object]:
        raw: dict[str, object] = {}
        errors: list[str] = []
        for index, (name, eoj, epc, field, decoder) in enumerate(PROPERTIES):
            try:
                edt = self._reader.get(eoj, epc)
                raw[field] = decoder(edt)
                if name == "battery_state":
                    raw["battery_operating_state_raw"] = unsigned_1(edt)
            except Exception as error:
                errors.append(f"{name}_read_failed")
                LOGGER.warning("ECHONET property read failed name=%s: %s", name, error)
            if index < len(PROPERTIES) - 1:
                self._sleep(self._property_interval)
        message = normalize(raw, device_id=self._device_id, measured_at=self._now(), errors=errors)
        LOGGER.info("Collection completed quality=%s errors=%s", message["quality"], len(message["errors"]))
        return message
