"""Best-effort MQTT publisher following OMK topic and status conventions."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:
    mqtt = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


class MqttPublisher:
    def __init__(self, *, device_id: str, host: str, port: int, keepalive: int,
                 client_factory: Callable[..., Any] | None = None) -> None:
        if client_factory is None:
            if mqtt is None:
                raise RuntimeError("paho-mqtt is required; install requirements.txt")
            client_factory = mqtt.Client
        self.device_id, self.host, self.port, self.keepalive = device_id, host, port, keepalive
        self._connected = False
        self._closed = False
        kwargs: dict[str, Any] = {"client_id": f"omk-ichijo-energy-{device_id}"}
        if mqtt is not None:
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        self._client = client_factory(**kwargs)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.max_queued_messages_set(1)
        self._client.will_set(self.status_topic, self._status_payload("offline"), qos=0, retain=True)

    @property
    def topic_base(self) -> str:
        return f"omk/{self.device_id}"

    @property
    def power_flow_topic(self) -> str:
        return f"{self.topic_base}/power-flow"

    @property
    def status_topic(self) -> str:
        return f"{self.topic_base}/status"

    def start(self) -> None:
        try:
            self._client.connect_async(self.host, self.port, keepalive=self.keepalive)
            self._client.loop_start()
        except Exception:
            # Collection remains useful during a broker outage; messages are non-durable by design.
            LOGGER.warning("Unable to start MQTT client; reconnecting is unavailable until restart", exc_info=True)

    def publish_power_flow(self, payload: dict[str, object]) -> None:
        self._publish(self.power_flow_topic, payload, retain=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._connected:
            try:
                result = self._client.publish(self.status_topic, self._status_payload("offline"), qos=0, retain=True)
                if getattr(result, "rc", 0) == 0:
                    result.wait_for_publish(timeout=1.0)
            except Exception:
                LOGGER.warning("Unable to publish MQTT offline status", exc_info=True)
        try:
            self._client.loop_stop()
            self._client.disconnect()
        finally:
            self._connected = False

    def _on_connect(self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if getattr(reason_code, "is_failure", False) or reason_code != 0:
            LOGGER.warning("MQTT connection refused: %s", reason_code)
            return
        reconnected = self._connected
        self._connected = True
        LOGGER.info("MQTT %s host=%s:%s", "reconnected" if reconnected else "connected", self.host, self.port)
        self._publish(self.status_topic, {"device_id": self.device_id, "status": "online", "timestamp": self._timestamp()}, retain=True)

    def _on_disconnect(self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if self._connected and not self._closed:
            LOGGER.warning("MQTT disconnected: %s", reason_code)
        self._connected = False

    def _publish(self, topic: str, payload: dict[str, object], *, retain: bool) -> None:
        if not self._connected:
            LOGGER.debug("MQTT publish skipped while disconnected topic=%s", topic)
            return
        try:
            result = self._client.publish(topic, json.dumps(payload, ensure_ascii=False, allow_nan=False), qos=0, retain=retain)
            if getattr(result, "rc", 0) != 0:
                LOGGER.warning("MQTT publish failed topic=%s rc=%s", topic, result.rc)
        except Exception:
            LOGGER.warning("MQTT publish failed topic=%s", topic, exc_info=True)

    def _status_payload(self, status: str) -> str:
        return json.dumps({"device_id": self.device_id, "status": status, "timestamp": self._timestamp()}, ensure_ascii=False)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(JST).isoformat(timespec="seconds")
