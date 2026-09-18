"""SwitchBot passive advertisement identification and conservative decoding.

Model selection uses service device types or an existing physical-device hint.
Observed manufacturer layouts validate the selected model; they never discover
a model on their own. See docs/switchbot-model-evidence.md for evidence limits.
"""
from __future__ import annotations

import math
from typing import Any

from .models import DecodedAdvertisement

SWITCHBOT_COMPANY_ID = 0x0969
METER_SERVICE_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"
MODEL_BY_TYPE = {
    0x54: ("temperature_humidity_sensor", "environment"),
    0x69: ("temperature_humidity_sensor", "environment"),
}
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
# <physical id 6> <variable> <PIR status> <variable> <counter>. The official
# Motion Sensor specification defines PIR state at status bit 6. The known
# status structure has lower six bits 0x2c. Index 8 was initially observed as
# zero, but varies in later captures, so it is not a classifier condition.
MOTION_MANUFACTURER_LENGTH = 10
MOTION_STATUS_INDEX = 7
MOTION_STATUS_LOW_BITS = 0x2C

CONTACT_SERVICE_DEVICE_TYPE = 0x64
CONTACT_MANUFACTURER_LENGTH = 13
CONTACT_MANUFACTURER_STATUS_INDEX = 7

# Plug Mini layout: <physical id 6> <sequence> <switch state> <variable>
# <variable> <power big-endian 2>. Bytes 8/9 vary; neither identifies a model.
PLUG_MANUFACTURER_LENGTH = 12
PLUG_STATE_INDEX = 7

# Presence Sensor Pro has both a 12+7-byte manufacturer/service form and an
# observed manufacturer-only form. Neither layout is a unique classifier: the
# latter is decoded only after a physical-device hint or operator confirmation.
PRESENCE_MANUFACTURER_LENGTH = 12
PRESENCE_SERVICE_DATA_LENGTH = 7
PRESENCE_STATUS_INDEX = 7
PRESENCE_LIGHT_LEVEL_INDEX = 11
PRESENCE_BATTERY_PERCENT_INDEX = 2


def _hex_map(values: dict[str, bytes]) -> dict[str, str]:
    return {key: value.hex() for key, value in values.items()}


def device_key_for(address: str, manufacturer_data: dict[int, bytes]) -> str:
    """Return the same stable physical key used by ``decode()``."""
    company_data = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    physical_id = company_data[:6].hex() if company_data and len(company_data) >= 6 else address.replace(":", "").lower()
    return f"switchbot:{physical_id}"


def _decode_service_data(data: bytes | None) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the existing documented Meter service-data layout."""
    if not data or len(data) != 6 or (data[0] & 0x7F) not in MODEL_BY_TYPE:
        return None
    model, sensor_type = MODEL_BY_TYPE[data[0] & 0x7F]
    # SwitchBot Meter specification: byte 2 is battery, byte 3 contains the
    # fractional temperature digit, byte 4 contains sign/integer temperature,
    # and byte 5 contains the humidity (and temperature scale).
    measurement = _decode_meter_expression(data[3] & 0x0F, data[4], data[5] & 0x7F)
    if measurement is None:
        return None
    temperature, humidity = measurement
    return model, sensor_type, {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
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
    if not data or len(data) != 9 or data[0] != CONTACT_SERVICE_DEVICE_TYPE:
        return None
    state = _contact_state_from_sensor_data(data[3])
    if state is None:
        return None
    return "contact_sensor", "contact", {"contact_state": state}


def _decode_presence_sensor_data(
    manufacturer_data: bytes | None, service_data: bytes | None,
) -> tuple[str, str, dict[str, Any]] | None:
    """Validate a Presence layout AFTER model selection.

    Pi internal Bluetooth can receive the 12-byte manufacturer advertisement
    without fd3d service data. That form has no battery value.
    """
    if (
        manufacturer_data is None
        or len(manufacturer_data) != PRESENCE_MANUFACTURER_LENGTH
        or manufacturer_data[PRESENCE_STATUS_INDEX] & ~0xCC
    ):
        return None
    if service_data is not None and len(service_data) != PRESENCE_SERVICE_DATA_LENGTH:
        return None
    motion_state = 1 if manufacturer_data[PRESENCE_STATUS_INDEX] & 0x40 else 0
    light_level = manufacturer_data[PRESENCE_LIGHT_LEVEL_INDEX] & 0x0F
    # Bit 7 is LED state; bits 4..6 are currently uninterpreted. They are not
    # validity bits and must not discard an otherwise valid advertisement.
    if service_data is not None:
        values = {
            "motion_state": motion_state,
            "battery_percent": service_data[PRESENCE_BATTERY_PERCENT_INDEX] & 0x7F,
            "light_level": light_level,
        }
    else:
        values = {"motion_state": motion_state, "light_level": light_level}
    return "presence_sensor", "motion", values


def _decode_meter_expression(fraction: int, signed_integer: int, humidity: int) -> tuple[float, int] | None:
    """Decode the observed Meter-compatible temperature/humidity triplet."""
    integer = signed_integer & 0x7F
    if fraction > 9:
        return None
    temperature = integer + fraction / 10
    if (signed_integer & 0x80) == 0:
        temperature *= -1
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
    if fraction > 9:
        return None
    temperature = (integer_byte & 0x7F) + fraction / 10
    if not integer_byte & 0x80:
        temperature *= -1
    return "waterproof_sensor", "environment", {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
    }


def _decode_plug_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the selected Plug Mini; flag/counter bytes are not model IDs."""
    if len(data) != PLUG_MANUFACTURER_LENGTH:
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
    if len(data) != MOTION_MANUFACTURER_LENGTH:
        return None
    status = data[MOTION_STATUS_INDEX]
    if (status & 0x3F) != MOTION_STATUS_LOW_BITS:
        return None
    return "motion_sensor", "motion", {"motion_state": 1 if status & 0x40 else 0}


def _decode_contact_manufacturer_data(data: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """Decode the observed manufacturer-only Contact Sensor layout safely."""
    if len(data) != CONTACT_MANUFACTURER_LENGTH:
        return None
    # Manufacturer bits 4..5 carry the same HAL state meaning as service
    # data: 0x4c/0xcc => closed, 0x5c/0xdc => open, 0x6c =>
    # timeout-not-close (still physically open). The other bytes include
    # time/counter fields and vary in long-running observations, so they are
    # intentionally not classifier conditions.
    status = data[CONTACT_MANUFACTURER_STATUS_INDEX]
    if (status & 0x0F) != 0x0C:
        return None
    state = _contact_state_from_hal_state((status >> 4) & 0x03)
    if state is None:
        return None
    return "contact_sensor", "contact", {"contact_state": state}


# A: BLE API device-type table. Meter and Meter Plus intentionally share the
# existing OMK model/schema. A vendor implementation at commit d2eafbaa also
# assigns 0x35 to CO2 and 0x70 to Presence; these are version-scoped types, not
# inferred from OMK capture lengths. Do not import aliases from unrelated SDK
# versions without validating their wire layouts (e.g. newer enum 0x77 CO2).
SERVICE_MODELS = {
    0x54: "temperature_humidity_sensor", 0x69: "temperature_humidity_sensor",
    0x73: "motion_sensor", 0x64: "contact_sensor",
    0x67: "plug_sensor", 0x6A: "plug_sensor",
    0x77: "waterproof_sensor",  # vendor-hosted community layout + OMK capture
    0x35: "co2_sensor", 0x70: "presence_sensor",
}
MANUFACTURER_DECODERS = {
    "temperature_humidity_sensor": _decode_meter_manufacturer_data,
    "co2_sensor": _decode_co2_manufacturer_data,
    "waterproof_sensor": _decode_waterproof_manufacturer_data,
    "plug_sensor": _decode_plug_manufacturer_data,
    "motion_sensor": _decode_motion_manufacturer_data,
    "contact_sensor": _decode_contact_manufacturer_data,
}


def _select_model(service: bytes | None, hint: str | None) -> tuple[str | None, str]:
    """Select evidence before inspecting measurement values or layout candidates."""
    if service is not None:
        if not service:
            return None, "invalid_service"
        model = SERVICE_MODELS.get(service[0] & 0x7F)
        # C: observed Presence broadcast has no established unique model ID.
        # Only this explicitly supported encoding can supplement a Presence
        # hint; an arbitrary unknown service type must not be ignored.
        if service[:2] == b"\x00\x20" and hint == "presence_sensor":
            return hint, "registered_hint"
        if model is None:
            return None, "unsupported_service_type"
        if hint is not None and hint != model:
            return None, "conflicting_model_hint"
        return model, "service_type"
    if hint in MANUFACTURER_DECODERS or hint == "presence_sensor":
        return hint, "registered_hint"
    return None, "insufficient_model_evidence"


def _values_valid(values: dict[str, Any]) -> bool:
    """OMK accepted measurement ranges; never evidence of a device's model."""
    ranges = {
        "temperature_c": (-20, 60), "relative_humidity_percent": (0, 100),
        "battery_percent": (0, 100), "co2_ppm": (CO2_MIN_PPM, CO2_MAX_PPM),
        "motion_state": (0, 1), "contact_state": (0, 1),
        "switch_state": (0, 1), "light_level": (0, 15), "power_w": (0, 3276.7),
    }
    return all(
        key in ranges and isinstance(value, (int, float)) and math.isfinite(value)
        and ranges[key][0] <= value <= ranges[key][1]
        for key, value in values.items()
    )


def _decode_selected(
    model: str, company: bytes | None, service: bytes | None,
) -> tuple[str, str, dict[str, Any]] | None:
    """Validate only the selected model. Invalid input never tries another model."""
    service_result = None
    if service is not None:
        if len(service) < 3 or (service[2] & 0x7F) > 100:
            return None
        if model == "temperature_humidity_sensor":
            service_result = _decode_service_data(service)
        elif model == "contact_sensor":
            # Bit 7 is encryption in the Contact specification; no decryptor.
            service_result = _decode_contact_service_data(service)
        elif model == "motion_sensor":
            if (len(service) != 6 or service[1] & 0x3F
                    or service[5] & 0x40 or (service[5] & 0x0C) == 0x0C
                    or (service[5] & 0x03) not in (1, 2)):
                return None
            service_result = (model, "motion", {"motion_state": int(bool(service[1] & 0x40))})
        elif model == "presence_sensor":
            # Only the pinned vendor type or the explicitly supported hinted
            # 00/20 encoding reaches this point. Unparsed service tail fields
            # are not guessed to be model identifiers or measurement values.
            return _decode_presence_sensor_data(company, service)
        else:
            # CO2 supports the observed manufacturer layout with a short type
            # header only. The vendor's alternative 7-byte mode is not decoded.
            if len(service) != 3:
                return None
            sensor_type = "power" if model == "plug_sensor" else "environment"
            service_result = (model, sensor_type, {})
        if service_result is None or not _values_valid(service_result[2]):
            return None
    if model == "presence_sensor":
        return _decode_presence_sensor_data(company, None)
    # Up to six bytes can carry identification only. Motion is an exception:
    # service type 0x73 identifies the model, while OMK captures show its
    # manufacturer status changing later than the service snapshot. A malformed
    # manufacturer payload therefore falls back to already validated service
    # data; it never changes the selected model.
    manufacturer_result = None
    if company is not None and len(company) > 6:
        manufacturer_result = MANUFACTURER_DECODERS[model](company)
        if manufacturer_result is None or not _values_valid(manufacturer_result[2]):
            if model == "motion_sensor" and service_result is not None:
                return service_result
            return None
    if model == "motion_sensor":
        return manufacturer_result if manufacturer_result is not None else service_result
    if model == "temperature_humidity_sensor":
        return service_result if service_result is not None else manufacturer_result
    # Contact's service HAL state can be stale (OMK captures). Once service has
    # selected Contact, prefer the validated manufacturer's newer state snapshot.
    return manufacturer_result if manufacturer_result is not None else service_result


def decode(address: str, rssi: int, manufacturer_data: dict[int, bytes], service_data: dict[str, bytes], received_at: str,
           model_hint: str | None = None) -> DecodedAdvertisement | None:
    """Identify SwitchBot and safely decode either supported advertisement form."""
    company_data = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    normalized: dict[str, bytes] = {}
    conflicting_service = False
    for key, value in service_data.items():
        normalized_key = key.lower()
        if normalized_key in normalized and normalized[normalized_key] != value:
            conflicting_service = True
        normalized[normalized_key] = value
    service_bytes = normalized.get(METER_SERVICE_UUID)
    if company_data is None and service_bytes is None:
        return None

    raw = {
        "manufacturer_data": _hex_map({f"{key:04x}": value for key, value in manufacturer_data.items()}),
        "service_data": _hex_map(service_data),
    }
    selected_model, evidence = ((None, "ambiguous_service") if conflicting_service
                                else _select_model(service_bytes, model_hint))
    decoded = _decode_selected(selected_model, company_data, service_bytes) if selected_model else None
    if decoded is not None and not _values_valid(decoded[2]):
        decoded = None
    raw["classification"] = {
        "evidence": evidence,
        "status": "decoded" if decoded is not None else (
            "invalid_payload" if selected_model is not None else "unknown"
        ),
    }
    model, sensor_type, values = decoded or ("unknown_switchbot", "unknown", {})

    # BlueZ's address and the observed SwitchBot manufacturer physical ID agree.
    # Prefer the latter when complete, preserving a stable physical identifier.
    return DecodedAdvertisement(
        device_key=device_key_for(address, manufacturer_data), vendor="switchbot", model=model,
        sensor_type=sensor_type, rssi=rssi, received_at=received_at, values=values, raw=raw,
    )


# Manufacturer-only advertisements remain unknown until an operator confirms a
# model.  Every supported manufacturer decoder participates so an overlapping
# layout (for example Plug Mini and Waterproof Sensor) cannot be presented as
# a false one-model choice.
UNCONFIRMED_MODEL_NAMES = ("presence_sensor", *MANUFACTURER_DECODERS)


def _validated_unconfirmed_model(
    candidate: DecodedAdvertisement, model: str, evidence: str,
) -> DecodedAdvertisement | None:
    """Decode an operator-selectable legacy form without identifying it.

    Manufacturer-only layouts are observed, not unique identifiers.
    Validation provides a preview and a user-confirmed registration choice,
    never automatic model selection.
    """
    if (model not in UNCONFIRMED_MODEL_NAMES or candidate.vendor != "switchbot"
            or candidate.model != "unknown_switchbot"):
        return None
    try:
        manufacturer = {int(key, 16): bytes.fromhex(value)
                        for key, value in candidate.raw["manufacturer_data"].items()}
        service = {key: bytes.fromhex(value) for key, value in candidate.raw["service_data"].items()}
        supported = [value for key, value in service.items() if key.lower() == METER_SERVICE_UUID]
        supported_format = ((not service) or (len(supported) == 1 and supported[0].startswith(b"\x00\x20"))
                            if model == "presence_sensor" else not service)
        if not supported_format:
            return None
        validated = decode(candidate.device_key.removeprefix("switchbot:"), candidate.rssi,
                           manufacturer, service, candidate.received_at, model_hint=model)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if (validated is None or validated.model != model
            or validated.device_key != candidate.device_key or not validated.values):
        return None
    validated.raw["classification"]["evidence"] = evidence
    return validated


def unconfirmed_model_options(candidate: DecodedAdvertisement) -> dict[str, DecodedAdvertisement]:
    """Return one validation-gated user choice, or none if forms overlap."""
    options = {
        model: validated
        for model in UNCONFIRMED_MODEL_NAMES
        if (validated := _validated_unconfirmed_model(candidate, model, "unconfirmed_preview")) is not None
    }
    if "presence_sensor" in options:
        # A service field that only Presence currently understands must not hide
        # an independently valid Plug/Outdoor/etc. manufacturer layout. The
        # layout remains a manual choice only when no other model validates it.
        try:
            manufacturer = {int(key, 16): bytes.fromhex(value)
                            for key, value in candidate.raw["manufacturer_data"].items()}
            company = manufacturer.get(SWITCHBOT_COMPANY_ID)
            if company is not None and any(
                decoder(company) is not None for decoder in MANUFACTURER_DECODERS.values()
            ):
                return {}
        except (KeyError, TypeError, ValueError, AttributeError):
            return {}
    return options if len(options) == 1 else {}


def unconfirmed_registration_candidate(candidate: DecodedAdvertisement, model: str) -> DecodedAdvertisement | None:
    """Revalidate one explicit operator selection before saving its hint."""
    options = unconfirmed_model_options(candidate)
    validated = options.get(model)
    if validated is not None:
        validated.raw["classification"]["evidence"] = f"user_confirmed_{model}"
    return validated
