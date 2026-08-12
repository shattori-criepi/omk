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

# Captured on the Pi: <6-byte physical id> <variable> 03
# <tenths, signed integer, RH>. The first header byte changed from f4 to f8
# for the same physical Meter, so it is deliberately not a fixed discriminator.
METER_MANUFACTURER_LENGTH = 11
METER_MANUFACTURER_LAYOUT_MARKER_INDEX = 7
METER_MANUFACTURER_LAYOUT_MARKER = 0x03

# Captured Meter Pro CO2 layout: <MAC 6> <variable> e4 <Meter-compatible
# temperature/humidity 3> 00 <variable> <CO2 big-endian 2> 00. The variable
# byte before CO2 was 3f on one device and 24 on another, so it is not used.
CO2_MANUFACTURER_LENGTH = 16
CO2_LAYOUT_MARKER_INDEX = 7
CO2_LAYOUT_MARKER = 0xE4
CO2_TEMPERATURE_HUMIDITY_OFFSET = 8
CO2_MIN_PPM = 400
CO2_MAX_PPM = 10_000

# Pi captures of the Motion Sensor manufacturer advertisement:
# <physical id 6> <variable> <PIR status> 00 <counter>. The official Motion
# Sensor specification defines PIR state at status bit 6. The known status
# structure has lower six bits 0x2c; paired with the length and reserved byte
# this avoids treating existing Meter/CO2 layouts as Motion Sensor packets.
MOTION_MANUFACTURER_LENGTH = 10
MOTION_STATUS_INDEX = 7
MOTION_RESERVED_INDEX = 8
MOTION_STATUS_LOW_BITS = 0x2C


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


def _decode_meter_expression(fraction: int, signed_integer: int, humidity: int) -> tuple[float, int] | None:
    """Decode the observed Meter-compatible temperature/humidity triplet."""
    integer = signed_integer & 0x7F
    if fraction > 9 or humidity > 100:
        return None
    temperature = integer + fraction / 10
    if (signed_integer & 0x80) == 0:
        temperature *= -1
    # The documented Meter measurement range is -20.0 to 60.0 C. It makes the
    # manufacturer-layout identification stricter without assuming a MAC.
    if not -20 <= temperature <= 60:
        return None
    return temperature, humidity


def _decode_meter_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode Meter only when the complete observed manufacturer layout fits."""
    if len(data) != METER_MANUFACTURER_LENGTH or data[METER_MANUFACTURER_LAYOUT_MARKER_INDEX] != METER_MANUFACTURER_LAYOUT_MARKER:
        return None
    measurement = _decode_meter_expression(*data[8:11])
    if measurement is None:
        return None
    temperature, humidity = measurement
    return "meter", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
    }


def _decode_co2_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the observed two-device CO2 layout without a MAC-specific rule."""
    if len(data) != CO2_MANUFACTURER_LENGTH:
        return None
    if data[CO2_LAYOUT_MARKER_INDEX] != CO2_LAYOUT_MARKER or data[11] != 0 or data[15] != 0:
        return None
    measurement = _decode_meter_expression(*data[CO2_TEMPERATURE_HUMIDITY_OFFSET:11])
    if measurement is None:
        return None
    temperature, humidity = measurement
    co2_ppm = int.from_bytes(data[13:15], byteorder="big")
    if not CO2_MIN_PPM <= co2_ppm <= CO2_MAX_PPM:
        return None
    return "meter_pro_co2", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
        "co2_ppm": co2_ppm,
    }


def _decode_motion_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the verified Motion Sensor manufacturer advertisement layout."""
    if len(data) != MOTION_MANUFACTURER_LENGTH or data[MOTION_RESERVED_INDEX] != 0:
        return None
    status = data[MOTION_STATUS_INDEX]
    if (status & 0x3F) != MOTION_STATUS_LOW_BITS:
        return None
    return "motion_sensor", "motion", {"motion_state": 1 if status & 0x40 else 0}


def _decode_manufacturer_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    if not data:
        return None
    return (
        _decode_meter_manufacturer_data(data)
        or _decode_co2_manufacturer_data(data)
        or _decode_motion_manufacturer_data(data)
    )


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
