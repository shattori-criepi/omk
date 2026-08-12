"""SwitchBot passive advertisement identification and conservative decoding.

Decoders are intentionally separate by advertisement layout.  In particular,
manufacturer data layouts below are restricted to packets observed on OMK's Pi;
unknown SwitchBot advertisements remain raw candidates rather than guessed data.
"""
from __future__ import annotations

from typing import Any

from .models import DecodedAdvertisement

SWITCHBOT_COMPANY_ID = 0x0969
METER_SERVICE_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"
MODEL_BY_TYPE = {0x54: ("meter", "environment"), 0x69: ("meter_plus", "environment")}

# Captured on the Pi: <6-byte physical id> f4 03 <tenths, signed integer, RH>.
# f4 03 alone is *not* considered a device type; length and the complete,
# plausible measurement layout are required before classifying it as a Meter.
METER_MANUFACTURER_LENGTH = 11
METER_MANUFACTURER_HEADER = bytes.fromhex("f403")

# Captured Meter Pro CO2 layout.  The trailing zero and fixed separator make
# this intentionally narrower than a general "16 byte is CO2" assumption.
CO2_MANUFACTURER_LENGTH = 16
CO2_MANUFACTURER_SEPARATOR = 0x3F


def _hex_map(values: dict[str, bytes]) -> dict[str, str]:
    return {key: value.hex() for key, value in values.items()}


def _decode_service_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the existing documented Meter service-data layout."""
    if not data or len(data) < 6 or data[0] not in MODEL_BY_TYPE:
        return None
    model, sensor_type = MODEL_BY_TYPE[data[0]]
    # SwitchBot Meter specification: byte 2 is battery, byte 3 contains the
    # fractional temperature digit, byte 4 contains sign/integer temperature,
    # and byte 5 contains the humidity (and temperature scale).
    temperature = (data[4] & 0x7F) + (data[3] & 0x0F) / 10
    if (data[4] & 0x80) == 0:
        temperature *= -1
    return model, sensor_type, {
        "temperature_c": temperature,
        "relative_humidity_percent": data[5] & 0x7F,
        "battery_percent": data[2] & 0x7F,
    }


def _decode_meter_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode only the real Meter-compatible manufacturer packet layout."""
    if len(data) != METER_MANUFACTURER_LENGTH or data[6:8] != METER_MANUFACTURER_HEADER:
        return None
    tenths, signed_integer, humidity = data[8:11]
    integer = signed_integer & 0x7F
    # Reject a packet that only happens to have the header; the actual complete
    # layout is required. The sign bit is observed as positive in the fixture.
    if tenths > 9 or integer > 99 or humidity > 100:
        return None
    temperature = integer + tenths / 10
    if not signed_integer & 0x80:
        temperature *= -1
    return "meter", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
    }


def _decode_co2_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode only CO2 verified from two Pi captures; do not infer temp/RH."""
    if len(data) != CO2_MANUFACTURER_LENGTH:
        return None
    if data[12] != CO2_MANUFACTURER_SEPARATOR or data[15] != 0:
        return None
    co2_ppm = int.from_bytes(data[13:15], byteorder="big")
    return "meter_pro_co2", "environment", {"co2_ppm": co2_ppm}


def _decode_manufacturer_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    if not data:
        return None
    return _decode_meter_manufacturer_data(data) or _decode_co2_manufacturer_data(data)


def decode(address: str, rssi: int, manufacturer_data: dict[int, bytes], service_data: dict[str, bytes], received_at: str) -> DecodedAdvertisement | None:
    """Identify SwitchBot and safely decode either supported advertisement form."""
    company_data = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    normalized = {key.lower(): value for key, value in service_data.items()}
    service_bytes = normalized.get(METER_SERVICE_UUID)
    if company_data is None and service_bytes is None:
        return None

    raw = {
        "manufacturer_data": _hex_map({f"{key:04x}": value for key, value in manufacturer_data.items()}),
        "service_data": _hex_map(service_data),
    }
    decoded = _decode_service_data(service_bytes) or _decode_manufacturer_data(company_data)
    model, sensor_type, values = decoded or ("unknown_switchbot", "unknown", {})

    # BlueZ's address and the observed SwitchBot manufacturer physical ID agree.
    # Prefer the latter when complete, preserving a stable physical identifier.
    physical_id = company_data[:6].hex() if company_data and len(company_data) >= 6 else address.replace(":", "").lower()
    return DecodedAdvertisement(
        device_key=f"switchbot:{physical_id}", vendor="switchbot", model=model,
        sensor_type=sensor_type, rssi=rssi, received_at=received_at, values=values, raw=raw,
    )
