"""Persist every MQTT message beneath the configured topic as daily JSONL."""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # Allows storage logic tests without runtime dependencies.
    mqtt = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")
LATEST_FILES = {
    "power": "broute_power.json",
    "sen66": "sen66.json",
    "power-flow": "ichijo_power_flow.json",
}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


class JsonlWriter:
    """Append MQTT records to a file selected from the JST receive date."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.current_path: Path | None = None

    def write(self, record: dict[str, Any], received_at: datetime) -> bool:
        path = self.data_root / received_at.strftime("%Y") / received_at.strftime("%m") / (
            f"{received_at.strftime('%d')}.jsonl"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path != self.current_path:
                LOGGER.info("Switching sensor data file: %s", path)
                self.current_path = path
            with path.open("a", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                output.write("\n")
                output.flush()
            LOGGER.debug("Saved MQTT message topic=%s", record["topic"])
            return True
        except (OSError, TypeError, ValueError) as error:
            LOGGER.error("Failed to write sensor data file %s: %s", path, error)
            return False


class LatestDataWriter:
    """Atomically replace dashboard-oriented latest MQTT records."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def write(self, record: dict[str, Any]) -> None:
        filename = self._filename_for(record)
        if filename is None:
            return

        path = self.data_root / filename
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(record, temporary, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            LOGGER.debug("Updated latest MQTT data topic=%s path=%s", record["topic"], path)
        except (OSError, TypeError, ValueError) as error:
            LOGGER.error("Failed to write latest MQTT data %s: %s", path, error)
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Failed to remove temporary latest data file: %s", temporary_path)

    @staticmethod
    def _filename_for(record: dict[str, Any]) -> str | None:
        if "payload" not in record:
            return None
        topic = record.get("topic")
        if not isinstance(topic, str):
            return None
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "omk" or not parts[1]:
            return None
        return LATEST_FILES.get(parts[2])


def build_record(message: mqtt.MQTTMessage, received_at: datetime | None = None) -> dict[str, Any]:
    """Build a loss-aware JSONL record without interpreting payload fields."""
    received_at = received_at or datetime.now(JST)
    record: dict[str, Any] = {
        "received_at": received_at.isoformat(timespec="milliseconds"),
        "topic": message.topic,
        "qos": message.qos,
        "retain": message.retain,
    }
    try:
        text = message.payload.decode("utf-8")
    except UnicodeDecodeError:
        record["payload_base64"] = base64.b64encode(message.payload).decode("ascii")
        record["payload_encoding"] = "base64"
        return record

    try:
        record["payload"] = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        record["payload_raw"] = text
        record["payload_parse_error"] = f"{type(error).__name__}: {error.msg if isinstance(error, json.JSONDecodeError) else error}"
    return record


class SensorCollector:
    def __init__(self) -> None:
        if mqtt is None:
            raise RuntimeError("paho-mqtt is required; install requirements.txt")
        self.host = os.getenv("MQTT_HOST", "mosquitto")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.topic = os.getenv("MQTT_TOPIC", "omk/#")
        self.writer = JsonlWriter(Path(os.getenv("DATA_ROOT", "/app/data/sensors")))
        self.latest_writer = LatestDataWriter(Path(os.getenv("LATEST_DATA_ROOT", "/app/data/latest")))
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="omk-sensor-collector", clean_session=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT connection refused: %s", reason_code)
            return
        client.subscribe(self.topic, qos=0)
        LOGGER.info("Connected to MQTT broker %s:%s; subscribed to %s", self.host, self.port, self.topic)

    def _on_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        LOGGER.warning("MQTT connection failed; retrying with backoff")

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code == 0:
            LOGGER.info("Disconnected from MQTT broker")
        else:
            LOGGER.warning("MQTT disconnected (%s); reconnecting with backoff", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        received_at = datetime.now(JST)
        record = build_record(message, received_at)
        if "payload_parse_error" in record:
            LOGGER.warning("Payload parse error topic=%s: %s", message.topic, record["payload_parse_error"])
        elif "payload_encoding" in record:
            LOGGER.warning("Non-UTF-8 payload topic=%s; storing Base64", message.topic)
        if self.writer.write(record, received_at):
            self.latest_writer.write(record)

    def run(self) -> None:
        LOGGER.info("Starting sensor collector; MQTT broker=%s:%s topic=%s", self.host, self.port, self.topic)
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_forever(retry_first_connection=True)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    SensorCollector().run()


if __name__ == "__main__":
    main()
