from __future__ import annotations

import asyncio
import json
import logging
import os
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .models import DecodedAdvertisement, RegisteredSensor, now_iso
from .node_credentials import NodeCredentialStore
from .node_provisioning import NodeProvisioner
from .node_registry import NodeRegistry
from .omk_node import decode as decode_omk_node
from .registry import SensorRegistry
from .switchbot import decode

LOGGER = logging.getLogger(__name__)
ENVIRONMENT_PUBLISH_INTERVAL_SECONDS = 10.0
STATE_PUBLISH_INTERVAL_SECONDS = 10.0
POWER_PUBLISH_INTERVAL_SECONDS = 10.0
NODE_CAPABILITY_NAMES = ((1 << 0, "ble_scan"), (1 << 1, "sen66"))
DISCOVERY_CONTROL_START_UUID = "c1347091-4268-2fb1-884a-7d019a432155"
CONTROL_CONNECT_MAX_ATTEMPTS = 8
CONTROL_CONNECT_TIMEOUT_SECONDS = 4.0
CONTROL_REDISCOVERY_TIMEOUT_SECONDS = 2.0
CONTROL_CONNECT_RETRY_SECONDS = 0.25


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
        # BLEDevice is deliberately runtime-only. It avoids BleakClient's
        # address-string implicit discovery when a provisioning job pauses the
        # passive scanner before opening the Control GATT connection.
        self._node_devices: dict[str, Any] = {}
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
        credential_directory = Path(os.getenv("OMK_NODE_CREDENTIAL_DIRECTORY", "data/provisioning/nodes"))
        self._node_provisioner = NodeProvisioner(
            self.pause_collection, self.start_collection, self._node_effective_state,
            NodeCredentialStore(credential_directory), self._send_start_provisioning,
        )

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

    async def _send_start_provisioning(self, node_id: str) -> None:
        seen = self.node_observations.get(node_id)
        address = seen.raw.get("ble_address") if seen else None
        if not isinstance(address, str) or not address:
            raise RuntimeError("node is no longer visible over BLE")

        LOGGER.info("Starting OMK Node provisioning node_id=%s", node_id)
        # A cached device is valid for the first attempt only. A failed BlueZ
        # connection may leave it stale, so every later attempt explicitly
        # rediscovers the peripheral before creating a new client.
        ble_device = self._node_devices.get(node_id)
        last_error: Exception | None = None
        for attempt in range(1, CONTROL_CONNECT_MAX_ATTEMPTS + 1):
            client: Any | None = None
            if attempt > 1 or ble_device is None:
                try:
                    from bleak import BleakScanner
                    LOGGER.info(
                        "Rediscovering OMK Node control device node_id=%s attempt=%d/%d phase=rediscovery",
                        node_id,
                        attempt,
                        CONTROL_CONNECT_MAX_ATTEMPTS,
                    )
                    ble_device = await BleakScanner.find_device_by_address(
                        address,
                        timeout=CONTROL_REDISCOVERY_TIMEOUT_SECONDS,
                    )
                    if ble_device is None:
                        raise RuntimeError("node was not found during rediscovery")
                    self._node_devices[node_id] = ble_device
                except Exception as error:
                    last_error = error
                    LOGGER.warning(
                        "OMK Node control retry failed node_id=%s attempt=%d/%d phase=rediscovery",
                        node_id,
                        attempt,
                        CONTROL_CONNECT_MAX_ATTEMPTS,
                    )
                    if attempt < CONTROL_CONNECT_MAX_ATTEMPTS:
                        await asyncio.sleep(CONTROL_CONNECT_RETRY_SECONDS)
                    continue
            try:
                from bleak import BleakClient
                client = BleakClient(ble_device)
                LOGGER.info(
                    "Connecting to OMK Node control service node_id=%s attempt=%d/%d phase=connect",
                    node_id,
                    attempt,
                    CONTROL_CONNECT_MAX_ATTEMPTS,
                )
                await asyncio.wait_for(client.connect(), timeout=CONTROL_CONNECT_TIMEOUT_SECONDS)
                break
            except Exception as error:
                last_error = error
                LOGGER.warning(
                    "OMK Node control retry failed node_id=%s attempt=%d/%d phase=connect",
                    node_id,
                    attempt,
                    CONTROL_CONNECT_MAX_ATTEMPTS,
                )
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                if attempt < CONTROL_CONNECT_MAX_ATTEMPTS:
                    await asyncio.sleep(CONTROL_CONNECT_RETRY_SECONDS)
        else:
            # No START request could have reached the Node without a control
            # connection, so this remains a definite failure.
            raise RuntimeError("could not connect to node control service") from last_error

        try:
            # Keep a write request here: it is the characteristic's supported
            # operation, and lets the Node acknowledge START when it has time.
            # The Node may reboot immediately after accepting it, however, so a
            # missing response is not evidence that START was not delivered.
            LOGGER.info("Control START write attempted node_id=%s", node_id)
            try:
                await client.write_gatt_char(DISCOVERY_CONTROL_START_UUID, b"\x01", response=True)
            except Exception:
                LOGGER.info(
                    "Control connection ended after START; continuing to provisioning service node_id=%s",
                    node_id,
                )
        finally:
            try:
                await client.disconnect()
            except Exception:
                # A rebooted Node has already closed the link. Disconnect is
                # best-effort cleanup and must not change the START outcome.
                LOGGER.debug("Control disconnect completed after Node link closed node_id=%s", node_id)

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
                decoded = decode(device.address, advertisement.rssi, advertisement.manufacturer_data, advertisement.service_data, received_at)
            if decoded:
                if decoded.model == "omk_node":
                    self._node_devices[decoded.values["node_id"]] = device
                self.record_advertisement(decoded)
        except Exception:
            # A malformed packet must not prevent later BlueZ callbacks.
            LOGGER.exception("Ignoring malformed BLE advertisement")

    def record_advertisement(self, decoded: DecodedAdvertisement) -> None:
        """Record an advertisement without ever reordering setup candidates."""
        self.observations[decoded.device_key] = decoded
        if decoded.model == "omk_node":
            self.node_observations[decoded.values["node_id"]] = decoded
        registered_keys = {sensor.device_key for sensor in self.registry.list()}
        if self.scanning and decoded.device_key not in registered_keys:
            if decoded.device_key not in self.setup_candidates:
                self._setup_candidate_order.append(decoded.device_key)
            # Always replace the complete snapshot: RSSI, receive time, values,
            # raw packet, and visual highlighting all use the latest packet.
            self.setup_candidates[decoded.device_key] = decoded
        self._publish_if_registered(decoded)

    def node_list(self) -> list[dict[str, Any]]:
        persisted = self.node_registry.list() if self.node_registry else {}
        ids = sorted(set(persisted) | set(self.node_observations))
        result = []
        for node_id in ids:
            item = dict(persisted.get(node_id, {"node_id": node_id}))
            seen = self.node_observations.get(node_id)
            if seen:
                item.update({"capabilities": seen.values["capabilities"], "ble_state": seen.values["provisioning_state"],
                             "last_seen": seen.received_at, "ble_address": seen.raw.get("ble_address")})
            elif isinstance(item.get("capabilities"), int):
                item["capabilities"] = [name for bit, name in NODE_CAPABILITY_NAMES if item["capabilities"] & bit]
            item["registration_state"] = item.get("registration_state", item.get("ble_state", "unregistered"))
            job = self._node_provisioner.job(node_id)
            if job:
                item.update(job.as_dict())
            result.append(item)
        return result

    def _node_effective_state(self, node_id: str) -> str | None:
        item = next((item for item in self.node_list() if item["node_id"] == node_id), None)
        return item.get("registration_state") if item else None

    async def request_node_provisioning(self, node_id: str) -> dict[str, str]:
        if not self._node_id_valid(node_id):
            raise ValueError("invalid node_id")
        job = await self._node_provisioner.start(node_id)
        return {"node_id": node_id, **job.as_dict()}

    @staticmethod
    def _node_id_valid(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 12 and all(char in "0123456789abcdef" for char in value)

    @staticmethod
    def _logical_id_valid(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 48 and all(char.isascii() and (char.isalnum() or char in "-_") for char in value)

    def request_node_registration(self, node_id: str, logical_id: str) -> dict[str, Any]:
        if not self._node_id_valid(node_id) or not self._logical_id_valid(logical_id):
            raise ValueError("invalid node_id or logical_id")
        current = next((item for item in self.node_list() if item["node_id"] == node_id), None)
        if current is None:
            raise KeyError(node_id)
        if current.get("registration_state") == "registered":
            raise ValueError("node is already registered")
        if current.get("registration_state") != "provisioned":
            raise ValueError("node Wi-Fi provisioning is not complete")
        if not self._mqtt:
            raise RuntimeError("MQTT client is unavailable")
        payload = json.dumps({"protocol_version": 1, "logical_id": logical_id})
        info = self._mqtt.publish(f"omk/node/{node_id}/registration/config", payload, qos=1, retain=False)
        if getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT publish failed")
        if self.node_registry:
            self.node_registry.update(node_id, logical_id=logical_id, request_state="request_sent")
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
            if state not in {"provisioned", "registered"} or not isinstance(capabilities, int):
                return
            self.node_registry.update(parts[2], protocol_version=1, capabilities=capabilities,
                                      registration_state=state, mqtt_status_seen_at=now_iso())
            return
        logical_id = value.get("logical_id")
        if value.get("registration_state") != "registered" or not self._logical_id_valid(logical_id):
            return
        self.node_registry.update(parts[2], protocol_version=1, logical_id=logical_id,
                                  registration_state="registered", request_state="registered", ack_seen_at=now_iso())

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
