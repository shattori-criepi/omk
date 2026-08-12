"""SwitchBot passive advertisement identification and conservative decoding.

Only the public Meter/Meter Plus service-data layout is decoded here.  Unknown
models are retained as candidates with raw bytes so they can be verified on a
real device before adding a model-specific decoder.
"""
from __future__ import annotations

from typing import Any

from .models import DecodedAdvertisement

SWITCHBOT_COMPANY_ID = 0x0969
METER_SERVICE_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"
MODEL_BY_TYPE = {0x54: ("meter", "environment"), 0x69: ("meter_plus", "environment")}


def _hex_map(values: dict[str, bytes]) -> dict[str, str]:
    return {key: value.hex() for key, value in values.items()}


def decode(address: str, rssi: int, manufacturer_data: dict[int, bytes], service_data: dict[str, bytes], received_at: str) -> DecodedAdvertisement | None:
    """Identify SwitchBot and decode Meter data without assuming unknown layouts."""
    company_data = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    normalized = {key.lower(): value for key, value in service_data.items()}
    service_bytes = normalized.get(METER_SERVICE_UUID)
    if company_data is None and service_bytes is None:
        return None
    raw = {"manufacturer_data": _hex_map({f"{key:04x}": value for key, value in manufacturer_data.items()}), "service_data": _hex_map(service_data)}
    model, sensor_type = "unknown_switchbot", "unknown"
    values: dict[str, Any] = {}
    # Documented Meter family service data: type, flags, temp(0.1 C), humidity,
    # battery. Guard every index because advertisements are often truncated.
    if service_bytes and len(service_bytes) >= 6 and service_bytes[0] in MODEL_BY_TYPE:
        model, sensor_type = MODEL_BY_TYPE[service_bytes[0]]
        temperature = ((service_bytes[2] & 0x7F) * 10 + (service_bytes[3] & 0x0F)) / 10
        if service_bytes[2] & 0x80:
            temperature *= -1
        values = {
            "temperature_c": temperature,
            "relative_humidity_percent": service_bytes[4] & 0x7F,
            "battery_percent": service_bytes[5] & 0x7F,
        }
    return DecodedAdvertisement(
        device_key=f"switchbot:{address.replace(':', '').lower()}", vendor="switchbot", model=model,
        sensor_type=sensor_type, rssi=rssi, received_at=received_at, values=values, raw=raw,
    )
