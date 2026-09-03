from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from threading import RLock
from time import monotonic
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .models import DecodedAdvertisement, RegisteredSensor, now_iso
from .node_registry import NodeRegistry
from .omk_node import decode as decode_omk_node
from .registry import SensorRegistry
from .switchbot import decode, device_key_for

LOGGER = logging.getLogger(__name__)
ENVIRONMENT_PUBLISH_INTERVAL_SECONDS = 10.0
STATE_PUBLISH_INTERVAL_SECONDS = 10.0
POWER_PUBLISH_INTERVAL_SECONDS = 10.0
DIRECT_FRESHNESS_SECONDS = 30.0
NODE_CAPABILITY_NAMES = ((1 << 0, "ble_scan"), (1 << 1, "sen66"))


class BleManager:
    def __init__(
        self,
        registry: SensorRegistry,
        mqtt_client: mqtt.Client | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
        node_registry: NodeRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.node_registry = node_registry
        # Observations run continuously for registered-sensor health. Setup
        # candidate order and candidate payloads are deliberately separate:
        # updates never affect position during a session.
        self.observations: dict[str, DecodedAdvertisement] = {}
        self.node_observations: dict[str, DecodedAdvertisement] = {}
        self._previous_motion_state: dict[str, int] = {}
        self._previous_contact_state: dict[str, int] = {}
        self._last_environment_publish_at: dict[str, float] = {}
        self._last_state_publish_at: dict[str, float] = {}
        self._previous_switch_state: dict[str, int] = {}
        self._last_power_publish_at: dict[str, float] = {}
        self._last_direct_observation_at: dict[str, float] = {}
        self._last_canonical_source: dict[str, str] = {}
        self.setup_candidates: dict[str, DecodedAdvertisement] = {}
        self._setup_candidate_order: list[str] = []
        self.scanning = False
        self._scanner: Any = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._mqtt = mqtt_client
        self._node_registration_lock = RLock()
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

    async def pause_collection(self) -> None:
        """Release the adapter before the official provisioning client scans."""
        if self._scanner is None:
            return
        try:
            await self._scanner.stop()
        finally:
            self._scanner = None

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
            received_at = now_iso()
            decoded = decode_omk_node(advertisement.rssi, advertisement.service_data, received_at, device.address)
            if decoded is None:
                decoded = decode(device.address, advertisement.rssi, advertisement.manufacturer_data, advertisement.service_data, received_at,
                                 model_hint=self._registered_model_hint(device.address, advertisement.manufacturer_data))
            if decoded:
                self.record_advertisement(decoded)
        except Exception:
            # A malformed packet must not prevent later BlueZ callbacks.
            LOGGER.exception("Ignoring malformed BLE advertisement")

    def record_advertisement(self, decoded: DecodedAdvertisement, *, source: str = "direct",
                             relay_node_id: str | None = None) -> None:
        """Record an advertisement without ever reordering setup candidates."""
        if source == "direct":
            self.observations[decoded.device_key] = decoded
        if source == "direct" and decoded.model == "omk_node":
            self.node_observations[decoded.values["node_id"]] = decoded
        registered_keys = {sensor.device_key for sensor in self.registry.list()}
        if source == "direct" and self.scanning and decoded.device_key not in registered_keys:
            if decoded.device_key not in self.setup_candidates:
                self._setup_candidate_order.append(decoded.device_key)
            # Always replace the complete snapshot: RSSI, receive time, values,
            # raw packet, and visual highlighting all use the latest packet.
            self.setup_candidates[decoded.device_key] = decoded
        self._publish_if_registered(decoded, source=source, relay_node_id=relay_node_id)

    def node_list(self) -> list[dict[str, Any]]:
        persisted = self.node_registry.list() if self.node_registry else {}
        ids = sorted(set(persisted) | set(self.node_observations))
        result = []
        for node_id in ids:
            item = dict(persisted.get(node_id, {"node_id": node_id}))
            seen = self.node_observations.get(node_id)
            if seen:
                item.update({"capabilities": seen.values["capabilities"], "ble_state": seen.values["provisioning_state"],
                             "last_seen": seen.received_at, "ble_address": seen.raw.get("ble_address"),
                             "source": "omk_discovery"})
            elif isinstance(item.get("capabilities"), int):
                item["capabilities"] = [name for bit, name in NODE_CAPABILITY_NAMES if item["capabilities"] & bit]
            item["registration_state"] = item.get("registration_state", item.get("ble_state", "unregistered"))
            item["online"] = bool(item.get("mqtt_status_seen_at") or seen)
            item["attached_sensors"] = ["SEN66"] if "sen66" in item.get("connected_sensors", []) else []
            item["relay_active"] = bool(item.get("relay_last_seen_at"))
            result.append(item)
        return result

    @staticmethod
    def _node_id_valid(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 12 and all(char in "0123456789abcdef" for char in value)

    @staticmethod
    def _logical_id_valid(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 48 and all(char.isascii() and (char.isalnum() or char in "-_") for char in value)

    def _logical_id_owner(self, logical_id: str, *, excluding_node_id: str | None = None) -> str | None:
        if self.node_registry is None:
            return None
        for node_id, node in self.node_registry.list().items():
            if node_id != excluding_node_id and logical_id in {node.get("logical_id"), node.get("requested_logical_id")}:
                return node_id
        return None

    def request_node_registration(self, node_id: str, logical_id: str) -> dict[str, Any]:
        if not self._node_id_valid(node_id) or not self._logical_id_valid(logical_id):
            raise ValueError("invalid node_id or logical_id")
        with self._node_registration_lock:
            current = next((item for item in self.node_list() if item["node_id"] == node_id), None)
            if current is None:
                raise KeyError(node_id)
            if current.get("registration_state") not in {"provisioned", "registered"}:
                raise ValueError("node Wi-Fi provisioning is not complete")
            owner = self._logical_id_owner(logical_id, excluding_node_id=node_id)
            if owner is not None:
                raise ValueError(f"logical_id is already assigned to node {owner}")
            if not self._mqtt:
                raise RuntimeError("MQTT client is unavailable")
            payload = json.dumps({"protocol_version": 1, "logical_id": logical_id})
            info = self._mqtt.publish(f"omk/node/{node_id}/registration/config", payload, qos=1, retain=False)
            if getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError("MQTT publish failed")
            if self.node_registry:
                self.node_registry.update(node_id, requested_logical_id=logical_id, request_state="request_sent",
                                          registration_revoked=False)
        return {"node_id": node_id, "logical_id": logical_id, "status": "request_sent"}

    def handle_node_mqtt(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) != 5 or parts[:2] != ["omk", "node"] or parts[3] != "registration" or not self._node_id_valid(parts[2]):
            return
        if parts[4] not in {"status", "ack"} or self.node_registry is None:
            return
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Ignoring invalid OMK Node MQTT JSON")
            return
        if not isinstance(value, dict) or value.get("protocol_version") != 1 or value.get("node_id") != parts[2]:
            LOGGER.warning("Ignoring invalid OMK Node MQTT fields")
            return
        if parts[4] == "status":
            state = value.get("registration_state")
            capabilities = value.get("capabilities")
            connected_sensors = value.get("connected_sensors", [])
            if (state not in {"provisioned", "registered"} or not isinstance(capabilities, int) or
                    not isinstance(connected_sensors, list) or any(sensor != "sen66" for sensor in connected_sensors)):
                return
            status_values = {"protocol_version": 1, "capabilities": capabilities,
                             "connected_sensors": connected_sensors, "registration_state": state,
                             "mqtt_status_seen_at": now_iso()}
            if state == "provisioned":
                self.node_registry.clear_registration(parts[2], **status_values)
            else:
                self.node_registry.update(parts[2], **status_values)
            return
        logical_id = value.get("logical_id")
        if value.get("registration_state") != "registered" or not self._logical_id_valid(logical_id):
            return
        with self._node_registration_lock:
            current = self.node_registry.list().get(parts[2], {})
            if current.get("registration_revoked"):
                LOGGER.info("Ignoring retained registration ACK after removal for node_id=%s", parts[2])
                return
            requested = current.get("requested_logical_id")
            if requested is not None and requested != logical_id:
                LOGGER.warning("Ignoring stale registration ACK for node_id=%s", parts[2])
                return
            owner = self._logical_id_owner(logical_id, excluding_node_id=parts[2])
            if owner is not None:
                LOGGER.error("Ignoring duplicate OMK Node logical_id=%s from node_id=%s; already assigned to node_id=%s",
                             logical_id, parts[2], owner)
                return
            self.node_registry.update(parts[2], protocol_version=1, logical_id=logical_id,
                                      registration_state="registered", request_state="registered",
                                      requested_logical_id=None, ack_seen_at=now_iso(), registration_revoked=False)

    def handle_sen66_mqtt(self, topic: str, payload: bytes) -> None:
        """Mark a SEN66 as physically observed only after valid telemetry arrives."""
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "omk" or parts[2] != "sen66" or self.node_registry is None:
            return
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(value, dict) or value.get("device_id") != parts[1]:
            return
        for node_id, node in self.node_registry.list().items():
            if node.get("logical_id") == parts[1]:
                self.node_registry.update(node_id, sen66_last_seen_at=now_iso())

    def remove_node_registration(self, node_id: str) -> dict[str, Any]:
        if not self._node_id_valid(node_id) or self.node_registry is None:
            raise KeyError(node_id)
        with self._node_registration_lock:
            if node_id not in self.node_registry.list():
                raise KeyError(node_id)
            self.node_registry.clear_registration(node_id, registration_state="provisioned",
                                                  registration_revoked=True, removed_at=now_iso())
            if self._mqtt:
                payload = json.dumps({"protocol_version": 1, "logical_id": None})
                info = self._mqtt.publish(f"omk/node/{node_id}/registration/config", payload, qos=1, retain=True)
                if getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT publish failed")
        return {"node_id": node_id, "status": "removed"}

    def handle_relay_mqtt(self, topic: str, payload: bytes) -> None:
        """Reconstruct a raw observation and use the normal SwitchBot decoder."""
        if len(payload) > 512:
            LOGGER.warning("Ignoring oversized relay MQTT payload topic=%s", topic)
            return
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != "omk-relay" or parts[2:] != ["ble", "raw"]:
            return
        relay_node_id = parts[1]
        if not self._node_id_valid(relay_node_id):
            LOGGER.warning("Ignoring relay MQTT with invalid Node ID topic=%s", topic)
            return
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Ignoring invalid relay MQTT JSON topic=%s", topic)
            return
        if not isinstance(value, dict):
            LOGGER.warning("Ignoring non-object relay MQTT payload topic=%s", topic)
            return
        observation = self._parse_relay_observation(value, relay_node_id)
        if observation is None:
            LOGGER.warning("Ignoring invalid relay MQTT fields topic=%s", topic)
            return
        address, rssi, manufacturer_data, service_data = observation
        decoded = decode(address, rssi, manufacturer_data, service_data, now_iso(),
                         model_hint=self._registered_model_hint(address, manufacturer_data))
        if decoded is None or decoded.sensor_type == "unknown":
            LOGGER.debug("Ignoring unknown SwitchBot raw relay packet from node_id=%s", relay_node_id)
            return
        if self.node_registry:
            self.node_registry.update(relay_node_id, relay_last_seen_at=now_iso())
        self.record_advertisement(decoded, source="relay", relay_node_id=relay_node_id)

    @staticmethod
    def _parse_relay_observation(value: dict[str, Any], relay_node_id: str) -> tuple[str, int, dict[int, bytes], dict[str, bytes]] | None:
        if value.get("protocol_version") != 1 or value.get("relay_node_id") != relay_node_id:
            return None
        address, rssi = value.get("ble_address"), value.get("rssi")
        if (not isinstance(address, str) or len(address) != 12 or any(char not in "0123456789abcdef" for char in address) or
                isinstance(rssi, bool) or not isinstance(rssi, int) or not -127 <= rssi <= 20):
            return None
        manufacturer_values, service_values = value.get("manufacturer_data"), value.get("service_data")
        if not isinstance(manufacturer_values, list) or not isinstance(service_values, list) or len(manufacturer_values) > 4 or len(service_values) > 4:
            return None
        manufacturer_data: dict[int, bytes] = {}
        service_data: dict[str, bytes] = {}
        def parse_hex(raw: Any, maximum: int = 31) -> bytes | None:
            if not isinstance(raw, str) or len(raw) > maximum * 2 or len(raw) % 2 or any(char not in "0123456789abcdef" for char in raw): return None
            try: return bytes.fromhex(raw)
            except ValueError: return None
        for item in manufacturer_values:
            if not isinstance(item, dict) or isinstance(item.get("company_id"), bool) or not isinstance(item.get("company_id"), int) or not 0 <= item["company_id"] <= 0xffff:
                return None
            data = parse_hex(item.get("data"))
            if data is None or item["company_id"] in manufacturer_data: return None
            manufacturer_data[item["company_id"]] = data
        for item in service_values:
            uuid = item.get("uuid") if isinstance(item, dict) else None
            data = parse_hex(item.get("data")) if isinstance(item, dict) else None
            if not isinstance(uuid, str) or uuid.lower() != "0000fd3d-0000-1000-8000-00805f9b34fb" or data is None or uuid in service_data: return None
            service_data[uuid.lower()] = data
        if not manufacturer_data and not service_data: return None
        return address, rssi, manufacturer_data, service_data

    def _registered_model_hint(self, address: str, manufacturer_data: dict[int, bytes]) -> str | None:
        """Use immutable registry metadata only after deriving the normal key."""
        try:
            device_key = device_key_for(address, manufacturer_data)
            sensor = next((item for item in self.registry.list() if item.device_key == device_key), None)
        except Exception as error:
            LOGGER.error("Cannot read BLE registry for model hint: %s", error)
            return None
        return sensor.model if sensor else None

    @staticmethod
    def _switchbot_device_key_valid(value: Any) -> bool:
        prefix = "switchbot:"
        return (isinstance(value, str) and value.startswith(prefix) and len(value) == len(prefix) + 12 and
                all(character in "0123456789abcdef" for character in value[len(prefix):]))

    @staticmethod
    def _environment_values_valid(temperature_c: Any, humidity: Any) -> bool:
        return (not isinstance(temperature_c, bool) and isinstance(temperature_c, (int, float)) and
                math.isfinite(temperature_c) and -20.0 <= temperature_c <= 60.0 and
                not isinstance(humidity, bool) and isinstance(humidity, int) and 0 <= humidity <= 100)

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
        if candidate.model == "omk_node":
            raise ValueError("OMK Nodeの登録とWi-Fi provisioningはまだ実装されていません")
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
        if candidate.model == "omk_node":
            raise ValueError("OMK Nodeの登録とWi-Fi provisioningはまだ実装されていません")
        prefix = {
            "temperature_humidity_sensor": "th", "waterproof_sensor": "th", "co2_sensor": "co2",
            "motion_sensor": "motion", "presence_sensor": "motion", "contact_sensor": "contact", "plug_sensor": "plug",
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

    def _publish_if_registered(self, advertisement: DecodedAdvertisement, *, source: str,
                               relay_node_id: str | None) -> None:
        try:
            sensor = next((item for item in self.registry.list() if item.device_key == advertisement.device_key), None)
        except Exception as error:
            LOGGER.error("Ignoring BLE measurement because registry cannot be read: %s", error)
            return
        if not sensor or not advertisement.values:
            return
        now = self._monotonic_provider()
        if source == "direct":
            # This is observation freshness, deliberately independent from
            # canonical rate limiting and whether a value changed.
            self._last_direct_observation_at[sensor.device_key] = now
        elif source == "relay":
            last_direct = self._last_direct_observation_at.get(sensor.device_key)
            if last_direct is not None and now - last_direct < DIRECT_FRESHNESS_SECONDS:
                return
        else:
            return
        source_switched = self._last_canonical_source.get(sensor.device_key) not in (None, source)
        if sensor.sensor_type == "motion":
            self._publish_motion_change(sensor, advertisement, source, relay_node_id, source_switched)
            return
        if sensor.sensor_type == "contact":
            self._publish_contact_change(sensor, advertisement, source, relay_node_id, source_switched)
            return
        if sensor.sensor_type == "power":
            self._publish_power(sensor, advertisement, source, relay_node_id, source_switched)
            return
        if not sensor.enabled or not self._mqtt:
            return
        if sensor.sensor_type == "environment":
            published_at = self._last_environment_publish_at.get(sensor.device_key)
            now = self._monotonic_provider()
            if not source_switched and published_at is not None and now - published_at < ENVIRONMENT_PUBLISH_INTERVAL_SECONDS:
                return
        payload = {
            "device_id": sensor.sensor_id,
            "measured_at": advertisement.received_at,
            "quality": "normal",
            **advertisement.values,
        }
        payload["source"] = source
        if relay_node_id: payload["relay_node_id"] = relay_node_id
        self._mqtt.publish(f"omk/{sensor.sensor_id}/{sensor.sensor_type}", json.dumps(payload), qos=0, retain=False)
        self._last_canonical_source[sensor.device_key] = source
        if sensor.sensor_type == "environment":
            self._last_environment_publish_at[sensor.device_key] = now

    def _publish_power(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement, source: str,
                       relay_node_id: str | None, source_switched: bool) -> None:
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
        if not source_switched and previous == switch_state and last is not None and now - last < POWER_PUBLISH_INTERVAL_SECONDS:
            return
        payload = {"device_id": sensor.sensor_id, "measured_at": advertisement.received_at, "quality": "normal", **advertisement.values, "source": source}
        if relay_node_id: payload["relay_node_id"] = relay_node_id
        self._mqtt.publish(f"omk/{sensor.sensor_id}/power", json.dumps(payload), qos=0, retain=False)
        self._last_power_publish_at[sensor.device_key] = now
        self._last_canonical_source[sensor.device_key] = source

    def _publish_motion_change(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement, source: str,
                               relay_node_id: str | None, source_switched: bool) -> None:
        """Publish transitions immediately and the current state every 10 seconds."""
        state = advertisement.values.get("motion_state")
        if state not in (0, 1):
            return
        previous = self._previous_motion_state.get(sensor.device_key)
        self._previous_motion_state[sensor.device_key] = state
        self._publish_periodic_state(sensor, advertisement, state, previous, "motion_state", "motion", source, relay_node_id, source_switched)

    def _publish_contact_change(self, sensor: RegisteredSensor, advertisement: DecodedAdvertisement, source: str,
                                relay_node_id: str | None, source_switched: bool) -> None:
        """Publish transitions immediately and the current state every 10 seconds."""
        state = advertisement.values.get("contact_state")
        if state not in (0, 1):
            return
        previous = self._previous_contact_state.get(sensor.device_key)
        self._previous_contact_state[sensor.device_key] = state
        self._publish_periodic_state(sensor, advertisement, state, previous, "contact_state", "contact", source, relay_node_id, source_switched)

    def _publish_periodic_state(
        self,
        sensor: RegisteredSensor,
        advertisement: DecodedAdvertisement,
        state: int,
        previous: int | None,
        field: str,
        topic_kind: str,
        source: str,
        relay_node_id: str | None,
        source_switched: bool,
    ) -> None:
        if not sensor.enabled or not self._mqtt:
            return
        now = self._monotonic_provider()
        last = self._last_state_publish_at.get(sensor.device_key)
        if not source_switched and previous == state and last is not None and now - last < STATE_PUBLISH_INTERVAL_SECONDS:
            return
        payload = {
            "device_id": sensor.sensor_id,
            "measured_at": advertisement.received_at,
            **advertisement.values,
            "source": source,
        }
        if relay_node_id: payload["relay_node_id"] = relay_node_id
        self._mqtt.publish(f"omk/{sensor.sensor_id}/{topic_kind}", json.dumps(payload), qos=0, retain=False)
        self._last_state_publish_at[sensor.device_key] = now
        self._last_canonical_source[sensor.device_key] = source
