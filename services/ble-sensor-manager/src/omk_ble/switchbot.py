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
MODEL_BY_TYPE = {
    0x54: ("temperature_humidity_sensor", "environment"),
    0x69: ("temperature_humidity_sensor", "environment"),
}
WATERPROOF_SERVICE_DEVICE_TYPE = 0x77
# The public specification identifies Plug Mini as 0x67, while the verified
# domestic advertisement is 0x6a. Service data is only supplementary: the
# manufacturer layout is required to decode measurements.
PLUG_SERVICE_DEVICE_TYPES = {0x67, 0x6A}

# Captured on the Pi: <6-byte physical id> <variable> 03
# <tenths, signed integer, RH>. The first header byte changed from f4 to f8
# for the same physical Meter, so it is deliberately not a fixed discriminator.
METER_MANUFACTURER_LENGTH = 11
METER_MANUFACTURER_LAYOUT_MARKER_INDEX = 7
METER_MANUFACTURER_LAYOUT_MARKER = 0x03

# Captured Meter Pro CO2 layout is 16 bytes. Bytes 6, 7, 11, and 12 vary
# between captures; validate the Meter-compatible temperature/humidity triplet,
# big-endian CO2 value, and zero terminator instead.
CO2_MANUFACTURER_LENGTH = 16
CO2_TEMPERATURE_HUMIDITY_OFFSET = 8
CO2_MIN_PPM = 400
CO2_MAX_PPM = 10_000

# Waterproof Sensor layout: <physical id 6> <variable 2> <fraction/significant
# temperature digits 2> <humidity> <reserved 00>. It is deliberately distinct
# from the Meter layout despite using the same vendor ID.
WATERPROOF_MANUFACTURER_LENGTH = 12
WATERPROOF_TEMPERATURE_FRACTION_INDEX = 8
WATERPROOF_TEMPERATURE_INTEGER_INDEX = 9
WATERPROOF_HUMIDITY_INDEX = 10
WATERPROOF_RESERVED_INDEX = 11

# Pi captures of the Motion Sensor manufacturer advertisement:
# <physical id 6> <variable> <PIR status> 00 <counter>. The official Motion
# Sensor specification defines PIR state at status bit 6. The known status
# structure has lower six bits 0x2c; paired with the length and reserved byte
# this avoids treating existing Meter/CO2 layouts as Motion Sensor packets.
MOTION_MANUFACTURER_LENGTH = 10
MOTION_STATUS_INDEX = 7
MOTION_RESERVED_INDEX = 8
MOTION_STATUS_LOW_BITS = 0x2C

CONTACT_SERVICE_DEVICE_TYPE = 0x64
CONTACT_MANUFACTURER_LENGTH = 13
CONTACT_RESERVED_INDEX = 8
CONTACT_MANUFACTURER_STATUS_INDEX = 7

# Plug Mini layout: <physical id 6> <sequence> <switch state> <variable>
# <variable> <power big-endian 2>. Byte 8 was 0x16 on one device and 0x10 on
# another, so it is only retained as a strict manufacturer-only fallback marker.
PLUG_MANUFACTURER_LENGTH = 12
PLUG_STATE_INDEX = 7
PLUG_LAYOUT_MARKER_INDEX = 8
PLUG_LAYOUT_MARKER = 0x16

# Presence Sensor Pro advertisements combine a 12-byte manufacturer payload
# with a 7-byte SwitchBot fd3d service payload. This joint structure is the
# classifier: status, sequence, battery, and light values are deliberately not
# used to identify the model.
PRESENCE_MANUFACTURER_LENGTH = 12
PRESENCE_SERVICE_DATA_LENGTH = 7
PRESENCE_STATUS_INDEX = 7
PRESENCE_LIGHT_LEVEL_INDEX = 11
PRESENCE_BATTERY_PERCENT_INDEX = 2


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


def _contact_state_from_hal_state(hal_state: int) -> int | None:
    """Map Contact Sensor HAL states: closed=0, open/timeout-not-close=1."""
    if hal_state == 0:
        return 0
    if hal_state in (1, 2):
        return 1
    return None


def _contact_state_from_sensor_data(sensor_data: int) -> int | None:
    """Extract the service-data HAL state from bits 1..2."""
    return _contact_state_from_hal_state((sensor_data >> 1) & 0x03)


def _decode_contact_service_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    """Decode official Contact Sensor service data with device type 0x64."""
    if not data or len(data) < 4 or (data[0] & 0x7F) != CONTACT_SERVICE_DEVICE_TYPE:
        return None
    state = _contact_state_from_sensor_data(data[3])
    if state is None:
        return None
    return "contact_sensor", "contact", {"contact_state": state}


def _decode_waterproof_service_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    """Identify the waterproof temperature/humidity sensor's service data."""
    if not data or len(data) != 3 or data[0] != WATERPROOF_SERVICE_DEVICE_TYPE:
        return None
    return "waterproof_sensor", "environment", {}


def _decode_plug_service_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    """Identify the Plug Mini's short service-data advertisement."""
    if not data or len(data) != 3 or data[0] not in PLUG_SERVICE_DEVICE_TYPES:
        return None
    return "plug_sensor", "power", {}


def _decode_presence_sensor_data(
    manufacturer_data: bytes | None, service_data: bytes | None,
) -> tuple[str, str, dict[str, Any]] | None:
    """Decode Presence Sensor Pro only from its combined advertisement shape."""
    if (
        manufacturer_data is None
        or service_data is None
        or len(manufacturer_data) != PRESENCE_MANUFACTURER_LENGTH
        or len(service_data) != PRESENCE_SERVICE_DATA_LENGTH
    ):
        return None
    return "presence_sensor", "motion", {
        "motion_state": 1 if manufacturer_data[PRESENCE_STATUS_INDEX] & 0x40 else 0,
        "battery_percent": service_data[PRESENCE_BATTERY_PERCENT_INDEX] & 0x7F,
        "light_level": manufacturer_data[PRESENCE_LIGHT_LEVEL_INDEX] & 0x0F,
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
    return "temperature_humidity_sensor", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
    }


def _decode_co2_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the observed two-device CO2 layout without a MAC-specific rule."""
    if len(data) != CO2_MANUFACTURER_LENGTH:
        return None
    if data[15] != 0:
        return None
    measurement = _decode_meter_expression(*data[CO2_TEMPERATURE_HUMIDITY_OFFSET:11])
    if measurement is None:
        return None
    temperature, humidity = measurement
    co2_ppm = int.from_bytes(data[13:15], byteorder="big")
    if not CO2_MIN_PPM <= co2_ppm <= CO2_MAX_PPM:
        return None
    return "co2_sensor", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
        "co2_ppm": co2_ppm,
    }


def _decode_waterproof_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the verified waterproof temperature/humidity sensor layout."""
    if len(data) != WATERPROOF_MANUFACTURER_LENGTH or data[WATERPROOF_RESERVED_INDEX] != 0:
        return None
    fraction = data[WATERPROOF_TEMPERATURE_FRACTION_INDEX] & 0x0F
    integer_byte = data[WATERPROOF_TEMPERATURE_INTEGER_INDEX]
    humidity = data[WATERPROOF_HUMIDITY_INDEX] & 0x7F
    if fraction > 9 or humidity > 100:
        return None
    temperature = (integer_byte & 0x7F) + fraction / 10
    if not integer_byte & 0x80:
        temperature *= -1
    if not -20 <= temperature <= 60:
        return None
    return "waterproof_sensor", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
    }


def _decode_plug_manufacturer_data(
    data: bytes, *, require_marker: bool = True,
) -> tuple[str, str, dict[str, Any]] | None:
    """Decode Plug Mini values, with strict matching for manufacturer-only data."""
    if len(data) != PLUG_MANUFACTURER_LENGTH:
        return None
    if require_marker and data[PLUG_LAYOUT_MARKER_INDEX] != PLUG_LAYOUT_MARKER:
        return None
    state_byte = data[PLUG_STATE_INDEX]
    if state_byte & 0x7F:
        return None
    # Bit 7 of the power MSB is the official overload flag, not power data.
    raw_power = ((data[10] & 0x7F) << 8) | data[11]
    return "plug_sensor", "power", {
        "power_w": raw_power / 10.0,
        "switch_state": 1 if state_byte & 0x80 else 0,
    }


def _decode_motion_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the verified Motion Sensor manufacturer advertisement layout."""
    if len(data) != MOTION_MANUFACTURER_LENGTH or data[MOTION_RESERVED_INDEX] != 0:
        return None
    status = data[MOTION_STATUS_INDEX]
    if (status & 0x3F) != MOTION_STATUS_LOW_BITS:
        return None
    return "motion_sensor", "motion", {"motion_state": 1 if status & 0x40 else 0}


def _decode_contact_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the observed manufacturer-only Contact Sensor layout safely."""
    if len(data) != CONTACT_MANUFACTURER_LENGTH:
        return None
    # Captures show bytes 8 and 10 are reserved zeroes. The trailer varies
    # (for example 0x40, 0x80, 0xc0, 0x41, and 0xc1), so it is not a
    # classifier condition.
    if data[CONTACT_RESERVED_INDEX] != 0 or data[10] != 0:
        return None
    # Manufacturer bits 4..5 carry the same HAL state meaning as service
    # data: 0x4c/0xcc => closed, 0x5c/0xdc => open, 0x6c =>
    # timeout-not-close (still physically open). Bits 6..7 are not state.
    status = data[CONTACT_MANUFACTURER_STATUS_INDEX]
    if (status & 0x0F) != 0x0C:
        return None
    state = _contact_state_from_hal_state((status >> 4) & 0x03)
    if state is None:
        return None
    return "contact_sensor", "contact", {"contact_state": state}


def _decode_manufacturer_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    if not data:
        return None
    return (
        _decode_meter_manufacturer_data(data)
        or _decode_co2_manufacturer_data(data)
        or _decode_waterproof_manufacturer_data(data)
        or _decode_plug_manufacturer_data(data)
        or _decode_motion_manufacturer_data(data)
        or _decode_contact_manufacturer_data(data)
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
    # Evaluate this before manufacturer-only 12-byte layouts. Presence Sensor
    # Pro is identified by the complete manufacturer-plus-service structure;
    # Plug Mini and Waterproof Sensor use their distinct 3-byte service forms.
    presence = _decode_presence_sensor_data(company_data, service_bytes)
    # Contact service data reliably identifies the device, but the current Pi
    # captures show its state can be stale. When a complete Contact
    # manufacturer layout is available, prefer its state snapshot.
    contact_from_service = _decode_contact_service_data(service_bytes)
    contact_from_manufacturer = _decode_contact_manufacturer_data(company_data) if company_data else None
    waterproof_from_service = _decode_waterproof_service_data(service_bytes)
    waterproof_from_manufacturer = _decode_waterproof_manufacturer_data(company_data) if company_data else None
    plug_from_service = _decode_plug_service_data(service_bytes)
    # Domestic Plug Mini service data is observed as 0x6a (the public type is
    # 0x67). Once either identifies this device, byte 8 is deliberately not a
    # classifier: it varies across verified physical devices.
    plug_from_manufacturer = _decode_plug_manufacturer_data(
        company_data, require_marker=plug_from_service is None,
    ) if company_data else None
    decoded = (
        presence
        or contact_from_manufacturer
        or contact_from_service
        or waterproof_from_manufacturer
        or waterproof_from_service
        or plug_from_manufacturer
        or plug_from_service
        or _decode_service_data(service_bytes)
        or _decode_manufacturer_data(company_data)
    )
    model, sensor_type, values = decoded or ("unknown_switchbot", "unknown", {})

    # BlueZ's address and the observed SwitchBot manufacturer physical ID agree.
    # Prefer the latter when complete, preserving a stable physical identifier.
    physical_id = company_data[:6].hex() if company_data and len(company_data) >= 6 else address.replace(":", "").lower()
    return DecodedAdvertisement(
        device_key=f"switchbot:{physical_id}", vendor="switchbot", model=model,
        sensor_type=sensor_type, rssi=rssi, received_at=received_at, values=values, raw=raw,
    )
