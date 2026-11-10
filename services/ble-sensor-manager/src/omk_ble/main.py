from __future__ import annotations

import os
import asyncio
import logging
from contextlib import suppress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
import paho.mqtt.client as mqtt
from pydantic import BaseModel, ConfigDict, Field

from .registry import RegistryError, SensorRegistry
from .node_registry import NodeRegistry
from .service import BleManager

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="omk-ble-sensor-manager")
client.max_queued_messages_set(64)
manager = BleManager(
    SensorRegistry(Path(os.getenv("OMK_BLE_REGISTRY", "/var/lib/omk/ble/sensors.json"))),
    client,
    node_registry=NodeRegistry(Path(os.getenv("OMK_NODE_REGISTRY", "/var/lib/omk/ble/nodes.json"))),
)

class RegisterRequest(BaseModel):
    device_key: str
    sensor_id: str
    display_name: str = Field(min_length=1, max_length=64)
    location: str = Field(default="", max_length=64)
    enabled: bool = True
    confirmed_model: str | None = None


class UpdateSensorRequest(BaseModel):
    """Only logical registry settings are mutable from the management API."""
    model_config = ConfigDict(extra="forbid")
    sensor_id: str
    display_name: str = Field(min_length=1, max_length=64)
    location: str = Field(default="", max_length=64)
    enabled: bool

class NodeRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_id: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")

def _on_mqtt_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
    if reason_code == 0:
        client.subscribe("omk/node/+/status", qos=0)
        client.subscribe("omk/node/+/registration/status", qos=1)
        client.subscribe("omk/node/+/registration/ack", qos=1)
        client.subscribe("omk-relay/+/sen66/raw", qos=0)
        client.subscribe("omk-relay/+/ble/raw", qos=0)
        try:
            manager.mqtt_nodes_connected()
        except RuntimeError as error:
            logging.getLogger(__name__).warning("Node reconnect synchronization failed: %s", error)

def _on_mqtt_message(client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
    try:
        if message.topic.startswith("omk-relay/") and message.topic.endswith("/sen66/raw"):
            manager.handle_sen66_mqtt(message.topic, message.payload, retain=message.retain)
        elif message.topic.startswith("omk-relay/"):
            manager.handle_relay_mqtt(message.topic, message.payload, retain=message.retain)
        else:
            manager.handle_node_mqtt(message.topic, message.payload, retain=message.retain)
    except RuntimeError as error:
        # A transient persistence failure must not terminate Paho's network
        # thread; the durable desired state and next Node report drive retry.
        logging.getLogger(__name__).warning("Node message synchronization failed: %s", error)

async def _availability_loop() -> None:
    while True:
        try:
            manager.retry_pending_node_configs()
            manager.refresh_node_availability()
        except RuntimeError as error:
            logging.getLogger(__name__).warning("Node availability update failed: %s", error)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    availability_task = None
    try:
        client.on_connect = _on_mqtt_connect
        client.on_message = _on_mqtt_message
        client.connect_async(os.getenv("MQTT_HOST", "127.0.0.1"), int(os.getenv("MQTT_PORT", "1883")))
        client.loop_start()
        try:
            await manager.start_collection()
        except RuntimeError as error:
            # Absence of Bluetooth must not make the gateway service unhealthy.
            import logging
            logging.getLogger(__name__).warning("BLE passive collection unavailable: %s", error)
        availability_task = asyncio.create_task(_availability_loop())
        yield
    finally:
        if availability_task:
            availability_task.cancel()
            with suppress(asyncio.CancelledError):
                await availability_task
        try:
            await manager.pause_collection()
        finally:
            client.loop_stop()


app = FastAPI(title="OMK BLE sensor manager", lifespan=lifespan)

@app.post("/api/setup/scan")
async def start_scan() -> dict[str, Any]:
    try:
        await manager.start_scan(int(os.getenv("OMK_BLE_SCAN_SECONDS", "60")))
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    return {"status": "scanning", "timeout_seconds": int(os.getenv("OMK_BLE_SCAN_SECONDS", "60"))}

@app.delete("/api/setup/scan")
async def stop_scan() -> dict[str, str]:
    await manager.stop_scan()
    return {"status": "stopped"}

@app.get("/api/setup/candidates")
def candidates() -> dict[str, Any]:
    return {"scanning": manager.scanning, "candidates": manager.candidate_list()}


@app.get("/api/setup/suggested-sensor-id")
def suggested_sensor_id(device_key: str, confirmed_model: str | None = None) -> dict[str, str]:
    try:
        return {"sensor_id": manager.suggested_sensor_id(device_key, confirmed_model)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

@app.get("/api/sensors")
def sensors() -> dict[str, Any]:
    try:
        return {"sensors": manager.registered_list()}
    except RegistryError as error:
        raise HTTPException(500, str(error)) from error

@app.get("/api/nodes")
def nodes() -> dict[str, Any]:
    return {"nodes": manager.node_list()}

@app.post("/api/nodes/{node_id}/register", status_code=202)
def register_node(node_id: str, request: NodeRegistrationRequest) -> dict[str, Any]:
    try:
        return manager.request_node_registration(node_id, request.logical_id)
    except KeyError as error:
        raise HTTPException(404, "node was not found") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

@app.delete("/api/nodes/{node_id}/registration")
def remove_node_registration(node_id: str) -> dict[str, Any]:
    try:
        return manager.remove_node_registration(node_id)
    except KeyError as error:
        raise HTTPException(404, "node was not found") from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

@app.post("/api/sensors", status_code=201)
def register(request: RegisterRequest) -> dict[str, Any]:
    try:
        return manager.register(request.model_dump()).as_dict()
    except (RegistryError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.patch("/api/sensors/{device_key}")
def update_sensor(device_key: str, request: UpdateSensorRequest) -> dict[str, Any]:
    try:
        return manager.update_registered_sensor(device_key, request.model_dump()).as_dict()
    except KeyError as error:
        raise HTTPException(404, "sensor was not found") from error
    except RegistryError as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/api/sensors/{device_key}")
def delete_sensor(device_key: str) -> dict[str, Any]:
    try:
        deleted = manager.delete_registered_sensor(device_key)
    except KeyError as error:
        raise HTTPException(404, "sensor was not found") from error
    except RegistryError as error:
        raise HTTPException(500, str(error)) from error
    return {"deleted": True, "device_key": deleted.device_key, "sensor_id": deleted.sensor_id}

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
