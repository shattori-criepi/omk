from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from .models import DecodedAdvertisement, RegisteredSensor, now_iso
from .registry import SensorRegistry
from .switchbot import decode

LOGGER = logging.getLogger(__name__)


class BleManager:
    def __init__(self, registry: SensorRegistry, mqtt_client: mqtt.Client | None = None) -> None:
        self.registry = registry
        self.candidates: dict[str, DecodedAdvertisement] = {}
        self.scanning = False
        self._scanner: Any = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._mqtt = mqtt_client
        self.offline_seconds = int(os.getenv("OMK_BLE_OFFLINE_SECONDS", "900"))

    async def start_scan(self, timeout_seconds: int = 60) -> None:
        await self.start_collection()
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

    def _on_detection(self, device: Any, advertisement: Any) -> None:
        decoded = decode(device.address, advertisement.rssi, advertisement.manufacturer_data, advertisement.service_data, now_iso())
        if decoded:
            self.candidates[decoded.device_key] = decoded
            self._publish_if_registered(decoded)

    def registered_list(self) -> list[dict[str, Any]]:
        """Return registration records without ever deleting an offline sensor."""
        now = datetime.now(timezone.utc)
        result = []
        for sensor in self.registry.list():
            item = sensor.as_dict()
            seen = self.candidates.get(sensor.device_key)
            item["last_received_at"] = seen.received_at if seen else None
            if not seen:
                item["status"] = "offline"
            else:
                try:
                    age = (now - datetime.fromisoformat(seen.received_at).astimezone(timezone.utc)).total_seconds()
                    item["status"] = "normal" if age <= self.offline_seconds else "offline"
                except ValueError:
                    item["status"] = "offline"
            result.append(item)
        return result

    def candidate_list(self) -> list[dict[str, Any]]:
        registered = {item.device_key: item for item in self.registry.list()}
        items = []
        for candidate in self.candidates.values():
            item = candidate.as_dict()
            item["registered"] = candidate.device_key in registered
            item["identifier_suffix"] = candidate.device_key[-4:].upper()
            item["highlight"] = "value_changed" if candidate.values else None
            items.append(item)
        return sorted(items, key=lambda value: value["rssi"], reverse=True)

    def register(self, request: dict[str, Any]) -> RegisteredSensor:
        candidate = self.candidates.get(request["device_key"])
        if not candidate:
            raise ValueError("device was not found in the current setup scan")
        sensor = RegisteredSensor(
            device_key=candidate.device_key, sensor_id=request["sensor_id"], sensor_type=candidate.sensor_type,
            vendor=candidate.vendor, model=candidate.model, location=request.get("location", ""),
            display_name=request["display_name"], enabled=bool(request.get("enabled", True)),
        )
        return self.registry.register(sensor)

    def _publish_if_registered(self, advertisement: DecodedAdvertisement) -> None:
        if not self._mqtt:
            return
        try:
            sensor = next((item for item in self.registry.list() if item.device_key == advertisement.device_key and item.enabled), None)
        except Exception as error:
            LOGGER.error("Ignoring BLE measurement because registry cannot be read: %s", error)
            return
        if not sensor or not advertisement.values:
            return
        payload = {"sensor_id": sensor.sensor_id, "measured_at": advertisement.received_at, "quality": "normal", **advertisement.values}
        self._mqtt.publish(f"omk/{sensor.sensor_id}/{sensor.sensor_type}", json.dumps(payload), qos=0, retain=False)
