"""Decoder for unregistered OMK ESP Node provisioning advertisements.

The packet is deliberately only an identification beacon.  It never carries
Wi-Fi credentials, a site UUID, SORACOM data, or any other secret.
"""
from __future__ import annotations

from typing import Any

from .models import DecodedAdvertisement

OMK_NODE_SERVICE_UUID = "7d2a4d90-7b64-4e3a-9f37-95e77d7b5101"
PROTOCOL_VERSION = 1
PROVISIONING_STATES = {0: "unregistered", 1: "provisioned", 2: "registered"}
CAPABILITY_BLE_SCAN = 1 << 0
CAPABILITY_SEN66 = 1 << 1
_KNOWN_CAPABILITIES = CAPABILITY_BLE_SCAN | CAPABILITY_SEN66
_CAPABILITY_NAMES = (
    (CAPABILITY_BLE_SCAN, "ble_scan"),
    (CAPABILITY_SEN66, "sen66"),
)
_SERVICE_DATA_LENGTH = 10


def decode(
    rssi: int,
    service_data: dict[str, bytes],
    received_at: str,
    address: str | None = None,
) -> DecodedAdvertisement | None:
    """Decode the exact v1 OMK Node advertisement without secrets."""

    if not isinstance(service_data, dict):
        return None
    data = next(
        (value for key, value in service_data.items() if key.lower() == OMK_NODE_SERVICE_UUID),
        None,
    )
    if not isinstance(data, bytes) or len(data) != _SERVICE_DATA_LENGTH:
        return None
    protocol_version, provisioning_state = data[0], data[1]
    capabilities = int.from_bytes(data[2:4], byteorder="big")
    node_id = data[4:10]
    if (
        protocol_version != PROTOCOL_VERSION
        or provisioning_state not in PROVISIONING_STATES
        or capabilities & ~_KNOWN_CAPABILITIES
        or node_id == b"\x00" * 6
    ):
        return None
    capability_names = [name for bit, name in _CAPABILITY_NAMES if capabilities & bit]
    node_id_text = node_id.hex()
    return DecodedAdvertisement(
        device_key=f"omk-node:{node_id_text}",
        vendor="omk",
        model="omk_node",
        sensor_type="provisioning",
        rssi=rssi,
        received_at=received_at,
        values={
            "protocol_version": protocol_version,
            "node_id": node_id_text,
            "capabilities": capability_names,
            "provisioning_state": PROVISIONING_STATES[provisioning_state],
        },
        raw={"service_uuid": OMK_NODE_SERVICE_UUID, "ble_address": address},
    )
