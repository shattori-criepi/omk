"""Select the direct BLE route, with an ESP32 relay fallback."""

from __future__ import annotations

from typing import Any

DIRECT_FRESHNESS_SECONDS = 30.0


class BleRouteSelector:
    """Keep relay observations only while direct BLE is stale for a device."""

    def __init__(self, freshness_seconds: float = DIRECT_FRESHNESS_SECONDS) -> None:
        self.freshness_seconds = freshness_seconds
        self.last_direct_seen: dict[str, float] = {}

    def should_accept(self, device_id: str, source: str, received_monotonic: float) -> bool:
        if source == "direct":
            self.last_direct_seen[device_id] = received_monotonic
            return True
        if source != "relay":
            return True
        last_direct = self.last_direct_seen.get(device_id)
        return last_direct is None or received_monotonic - last_direct >= self.freshness_seconds


def should_store_record(record: dict[str, Any], selector: BleRouteSelector, received_monotonic: float) -> bool:
    """Apply route selection only to well-formed canonical BLE observations."""
    topic = record.get("topic")
    payload = record.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        return True
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "omk" or parts[2] not in {"environment", "motion", "contact", "power"} or not parts[1]:
        return True
    device_id = parts[1]
    if payload.get("device_id") != device_id:
        return True
    source = payload.get("source")
    if source not in ("direct", "relay"):
        return True
    return selector.should_accept(device_id, source, received_monotonic)
