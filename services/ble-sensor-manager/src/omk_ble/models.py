from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DecodedAdvertisement:
    device_key: str
    vendor: str
    model: str
    sensor_type: str
    rssi: int
    received_at: str
    values: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegisteredSensor:
    device_key: str
    sensor_id: str
    sensor_type: str
    vendor: str
    model: str
    location: str
    display_name: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RegisteredSensor":
        return cls(**value)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
