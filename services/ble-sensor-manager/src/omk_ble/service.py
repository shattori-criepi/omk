from __future__ import annotations

import asyncio
import json
import logging
import os
from time import monotonic
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .models import DecodedAdvertisement, RegisteredSensor, now_iso
from .registry import SensorRegistry
from .switchbot import decode

LOGGER = logging.getLogger(__name__)
ENVIRONMENT_PUBLISH_INTERVAL_SECONDS = 10.0
STATE_PUBLISH_INTERVAL_SECONDS = 10.0
POWER_PUBLISH_INTERVAL_SECONDS = 10.0


class BleManager:
    def __init__(
        self,
        registry: SensorRegistry,
        mqtt_client: mqtt.Client | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        self.registry = registry
        # Observations run continuously for registered-sensor health. Setup
        # candidate order and candidate payloads are deliberately separate:
        # updates never affect position during a session.
        self.observations: dict[str, DecodedAdvertisement] = {}
        self._previous_motion_state: dict[str, int] = {}
        self._previous_contact_state: dict[str, int] = {}
        self._last_environment_publish_at: dict[str, float] = {}
        self._last_state_publish_at: dict[str, float] = {}
        self._previous_switch_state: dict[str, int] = {}
        self._last_power_publish_at: dict[str, float] = {}
        self.setup_candidates: dict[str, DecodedAdvertisement] = {}
        self._setup_candidate_order: list[str] = []
        self.scanning = False
        self._scanner: Any = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._mqtt = mqtt_client
        self.offline_seconds = int(os.getenv("OMK_BLE_OFFLINE_SECONDS", "900"))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._monotonic_provider = monotonic_provider or monotonic

    async def start_scan(self, timeout_seconds: int = 60) -> None:
        await self.start_collection()
        self.begin_setup_session()
        self.scanning = True
        if self._timeout_task:
            self._timeout_task.cancel()
        self._timeout_task = asyncio.create_task(self._stop_after(timeout_seconds))

    async def start_collection(self) -> None:
        """Keep passive reception active; only setup candidate exposure is temporary."""
        if self._scanner:
            return
        try:
            from bleak import BleakScanner
            self._scanner = BleakScanner(detection_callback=self._on_detection)
            await self._scanner.start()
        except Exception as error:
            LOGGER.warning("BLE scan could not start: %s", error)
            raise RuntimeError("Bluetooth adapter or BlueZ is unavailable") from error

    async def _stop_after(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        await self.stop_scan()

    async def stop_scan(self) -> None:
        if self._timeout_task and self._timeout_task is not asyncio.current_task():
            self._timeout_task.cancel()
        self.scanning = False

    def begin_setup_session(self) -> None:
        """Reset only setup candidates; passive observations remain intact."""
        self.setup_candidates.clear()
        self._setup_candidate_order.clear()

    def _on_detection(self, device: Any, advertisement: Any) -> None:
        try:
            decoded = decode(device.address, advertisement.rssi, advertisement.manufacturer_data, advertisement.service_data, now_iso())
            if decoded:
                self.record_advertisement(decoded)
        except Exception:
            # A malformed packet must not prevent later BlueZ callbacks.
            LOGGER.exception("Ignoring malformed BLE advertisement")

    def record_advertisement(self, decoded: DecodedAdvertisement) -> None:
        """Record an advertisement without ever reordering setup candidates."""
        self.observations[decoded.device_key] = decoded
        registered_keys = {sensor.device_key for sensor in self.registry.list()}
        if self.scanning and decoded.device_key not in registered_keys:
            if decoded.device_key not in self.setup_candidates:
                self._setup_candidate_order.append(decoded.device_key)
            # Always replace the complete snapshot: RSSI, receive time, values,
            # raw packet, and visual highlighting all use the latest packet.
            self.setup_candidates[decoded.device_key] = decoded
        self._publish_if_registered(decoded)

    def registered_list(self) -> list[dict[str, Any]]:
        """Join immutable registry settings with in-memory latest observations."""
        now = self._now_provider()
        result = []
        for sensor in self.registry.list():
            item = sensor.as_dict()
            seen = self.observations.get(sensor.device_key)
            if not seen:
                item["latest"] = None
                item["online"] = False
                item["status"] = "unreceived"
            else:
                item["latest"] = {
                    "received_at": seen.received_at,
                    "rssi": seen.rssi,
                    "values": seen.values,
                }
                try:
                    age = (now - datetime.fromisoformat(seen.received_at).astimezone(timezone.utc)).total_seconds()
                    item["online"] = age <= self.offline_seconds
                except ValueError:
                    item["online"] = False
                item["status"] = "normal" if item["online"] else "offline"
            result.append(item)
        return result

    def candidate_list(self) -> list[dict[str, Any]]:
        registered_keys = {item.device_key for item in self.registry.list()}
        items = []
        for device_key in self._setup_candidate_order:
            candidate = self.setup_candidates.get(device_key)
            if candidate is None:
                continue
            # Registration can occur while an API response is in flight.
            if candidate.device_key in registered_keys:
                continue
            item = candidate.as_dict()
            item["identifier_suffix"] = candidate.device_key[-4:].upper()
            item["highlight"] = "value_changed" if candidate.values else None
            items.append(item)
        return items

    def register(self, request: dict[str, Any]) -> RegisteredSensor:
        candidate = self.setup_candidates.get(request["device_key"])
        if not candidate:
            raise ValueError("device was not found in the current setup scan")
        sensor = RegisteredSensor(
            device_key=candidate.device_key, sensor_id=request["sensor_id"], sensor_type=candidate.sensor_type,
            vendor=candidate.vendor, model=candidate.model, location=request.get("location", ""),
            display_name=request["display_name"], enabled=bool(request.get("enabled", True)),
        )
        registered = self.registry.register(sensor)
        # Remove it immediately; remaining candidates retain their order.
        self.setup_candidates.pop(candidate.device_key, None)
        self._setup_candidate_order.remove(candidate.device_key)
        return registered

    def suggested_sensor_id(self, device_key: str) -> str:
        """Suggest a type-plus-sequence OMK ID without changing registration."""
        candidate = self.setup_candidates.get(device_key)
        if not candidate:
            raise ValueError("device was not found in the current setup scan")
        prefix = {
            "temperature_humidity_sensor": "th", "waterproof_sensor": "th", "co2_sensor": "co2",
            "motion_sensor": "motion", "contact_sensor": "contact", "plug_sensor": "plug",
        }.get(candidate.model, "sensor")
        used_ids = {sensor.sensor_id for sensor in self.registry.list()}
        index = 1
        while f"{prefix}-{index:03d}" in used_ids:
            index += 1
        return f"{prefix}-{index:03d}"

    def update_registered_sensor(self, device_key: str, request: dict[str, Any]) -> RegisteredSensor:
        """Update logical registry fields; observations remain keyed by device_key."""
        return self.registry.update(
            device_key,
            sensor_id=request["sensor_id"], display_name=request["display_name"],
            location=request.get("location", ""), enabled=bool(request["enabled"]),
        )

    def delete_registered_sensor(self, device_key: str) -> RegisteredSensor:
        """Unregister a physical device without deleting observations or history."""
        return self.registry.delete(device_key)

    def _publish_if_registered(self, advertisement: DecodedAdvertisement) -> None:
        try:
            sensor = next((item for item in self.registry.list() if item.device_key == advertisement.device_key), None)
        except Exception as error:
            LOGGER.error("Ignoring BLE measurement because registry cannot be read: %s", error)
            return
        if not sensor or not advertisement.values:
            return
        if sensor.sensor_type == "motion":
            self._publish_motion_change(sensor, advertisement)
            return
        if sensor.sensor_type == "contact":
            self._publish_contact_change(sensor, advertisement)
            return
        if sensor.sensor_type == "power":
            self._publish_power(sensor, advertisement)
            return
        if not sensor.enabled or not self._mqtt:
            return
        if sensor.sensor_type == "environment":
            published_at = self._last_environment_publish_at.get(sensor.device_key)
            now = self._monotonic_provider()
            if published_at is not None and now - published_at < ENVIRONMENT_PUBLISH_INTERVAL_SECONDS:
                return
        payload = {"device_id": sensor.sensor_id, "measured_at": advertisement.received_at, "quality": "normal", **advertisement.values}
        self._mqtt.publish(f"omk/{sensor.sensor_id}/{sensor.sensor_type}", json.dumps(payload), qos=0, retain=False)
        if sensor.sensor_type == "environment":
            self._last_environment_publish_at[sensor.device_key] = now

    def _publish_power(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement) -> None:
        """Publish Plug power periodically, but switch transitions immediately."""
        switch_state = advertisement.values.get("switch_state")
        if switch_state not in (0, 1):
            return
        previous = self._previous_switch_state.get(sensor.device_key)
        self._previous_switch_state[sensor.device_key] = switch_state
        if not sensor.enabled or not self._mqtt:
            return
        now = self._monotonic_provider()
        last = self._last_power_publish_at.get(sensor.device_key)
        if previous == switch_state and last is not None and now - last < POWER_PUBLISH_INTERVAL_SECONDS:
            return
        payload = {"device_id": sensor.sensor_id, "measured_at": advertisement.received_at, "quality": "normal", **advertisement.values}
        self._mqtt.publish(f"omk/{sensor.sensor_id}/power", json.dumps(payload), qos=0, retain=False)
        self._last_power_publish_at[sensor.device_key] = now

    def _publish_motion_change(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement) -> None:
        """Publish transitions immediately and the current state every 10 seconds."""
        state = advertisement.values.get("motion_state")
        if state not in (0, 1):
            return
        previous = self._previous_motion_state.get(sensor.device_key)
        self._previous_motion_state[sensor.device_key] = state
        self._publish_periodic_state(sensor, advertisement, state, previous, "motion_state", "motion")

    def _publish_contact_change(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement) -> None:
        """Publish transitions immediately and the current state every 10 seconds."""
        state = advertisement.values.get("contact_state")
        if state not in (0, 1):
            return
        previous = self._previous_contact_state.get(sensor.device_key)
        self._previous_contact_state[sensor.device_key] = state
        self._publish_periodic_state(sensor, advertisement, state, previous, "contact_state", "contact")

    def _publish_periodic_state(
        self,
        sensor: RegisteredSensor,
        advertisement: DecodedAdvertisement,
        state: int,
        previous: int | None,
        field: str,
        topic_kind: str,
    ) -> None:
        if not sensor.enabled or not self._mqtt:
            return
        now = self._monotonic_provider()
        last = self._last_state_publish_at.get(sensor.device_key)
        if previous == state and last is not None and now - last < STATE_PUBLISH_INTERVAL_SECONDS:
            return
        payload = {"device_id": sensor.sensor_id, "measured_at": advertisement.received_at, field: state}
        self._mqtt.publish(f"omk/{sensor.sensor_id}/{topic_kind}", json.dumps(payload), qos=0, retain=False)
        self._last_state_publish_at[sensor.device_key] = now
