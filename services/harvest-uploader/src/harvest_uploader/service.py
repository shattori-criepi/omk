"""Runtime orchestration for MQTT ingestion, aggregation, and durable delivery."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from .aggregation import JST, MinuteAggregator
from .http_client import HarvestClient
from .queue import RetryQueue

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:
    mqtt = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


class Sender(Protocol):
    def send(self, payload: dict[str, Any]) -> None: ...


class Uploader:
    """Application service; its clock and sender are injectable for deterministic tests."""

    def __init__(self, queue: RetryQueue, sender: Sender, *, clock: Callable[[], datetime] | None = None, max_age_seconds: int = 3600) -> None:
        self.aggregator = MinuteAggregator()
        self.queue, self.sender = queue, sender
        self.clock = clock or (lambda: datetime.now(JST))
        self.max_age_seconds = max_age_seconds

    def receive(self, topic: str, payload_bytes: bytes, received_at: datetime | None = None) -> None:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            LOGGER.warning("Ignoring invalid MQTT payload topic=%s", topic)
            return
        completed = self.aggregator.ingest(topic, payload, received_at or self.clock())
        if completed is not None:
            self._queue_record(completed)

    def tick(self) -> None:
        now = self.clock()
        completed = self.aggregator.flush_due(now)
        if completed is not None:
            self._queue_record(completed, now)
        expired = self.queue.discard_expired(now, self.max_age_seconds)
        if expired:
            LOGGER.warning("Discarded %s Harvest records older than %s seconds", expired, self.max_age_seconds)
        self._deliver_one(now)

    def _queue_record(self, record: dict[str, Any], now: datetime | None = None) -> None:
        self.queue.enqueue(record, now or self.clock())
        LOGGER.info("Created one-minute Harvest record time=%s", record["time"])

    def _deliver_one(self, now: datetime) -> None:
        pending = self.queue.next_due(now)
        if pending is None:
            return
        item_id, payload = pending
        try:
            self.sender.send(payload)
        except Exception:
            self.queue.postpone(item_id, now)
            LOGGER.warning("Harvest send failed; queued for retry pending=%s", self.queue.count(), exc_info=True)
            return
        self.queue.mark_sent(item_id)
        LOGGER.info("Harvest send succeeded time=%s pending=%s", payload["time"], self.queue.count())


class MqttRuntime:
    def __init__(self, uploader: Uploader, *, host: str, port: int, client_id: str, topic: str = "omk/#", client_factory: Callable[..., Any] | None = None) -> None:
        if client_factory is None:
            if mqtt is None:
                raise RuntimeError("paho-mqtt is required; install requirements.txt")
            client_factory = mqtt.Client
        kwargs: dict[str, Any] = {"client_id": client_id}
        if mqtt is not None:
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        self.host, self.port, self.topic, self.uploader = host, port, topic, uploader
        self.client = client_factory(**kwargs)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    def run(self) -> None:
        LOGGER.info("Starting Harvest uploader MQTT broker=%s:%s topic=%s", self.host, self.port, self.topic)
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()
        try:
            while True:
                self.uploader.tick()
                time.sleep(1)
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code != 0:
            LOGGER.warning("MQTT connection refused: %s", reason_code)
            return
        client.subscribe(self.topic, qos=0)
        LOGGER.info("MQTT connected; subscribed to %s", self.topic)

    def _on_disconnect(self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        LOGGER.info("MQTT disconnected") if reason_code == 0 else LOGGER.warning("MQTT disconnected (%s); reconnecting", reason_code)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        try:
            self.uploader.receive(message.topic, message.payload)
        except Exception:
            LOGGER.exception("Unexpected MQTT message handling error topic=%s", message.topic)


def run_from_environment() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    timezone = os.getenv("TZ", "Asia/Tokyo")
    if timezone != "Asia/Tokyo":
        LOGGER.warning("Harvest timestamps always use Asia/Tokyo; TZ=%s only affects process environment", timezone)
    queue = RetryQueue(Path(os.getenv("HARVEST_QUEUE_PATH", "/app/data/harvest-uploader/queue.sqlite3")))
    uploader = Uploader(queue, HarvestClient(os.getenv("HARVEST_ENDPOINT", "http://harvest.soracom.io"), float(os.getenv("HARVEST_TIMEOUT_SECONDS", "10"))), max_age_seconds=int(os.getenv("HARVEST_RETRY_MAX_AGE_SECONDS", "3600")))
    MqttRuntime(uploader, host=os.getenv("MQTT_HOST", "mosquitto"), port=int(os.getenv("MQTT_PORT", "1883")), client_id=os.getenv("MQTT_CLIENT_ID", "omk-harvest-uploader"), topic=os.getenv("MQTT_TOPIC", "omk/#")).run()
