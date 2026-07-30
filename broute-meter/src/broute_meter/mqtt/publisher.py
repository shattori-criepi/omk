"""Best-effort MQTT publication for already persisted B-route readings."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

import paho.mqtt.client as mqtt

from broute_meter.config import MqttConfig
from broute_meter.models import (
    CumulativeEnergyReading,
    InstantaneousPowerReading,
    IntervalEnergyReading,
)

logger = logging.getLogger(__name__)


class MeasurementPublisher(Protocol):
    """Non-durable delivery boundary used by the measurement scheduler."""

    def start(self) -> None: ...

    def publish_instantaneous(self, reading: InstantaneousPowerReading) -> None: ...

    def publish_cumulative(self, reading: CumulativeEnergyReading) -> None: ...

    def publish_interval(self, reading: IntervalEnergyReading) -> None: ...

    def close(self) -> None: ...


class NullMeasurementPublisher:
    """No-op publisher used when MQTT is disabled."""

    def start(self) -> None:
        pass

    def publish_instantaneous(self, reading: InstantaneousPowerReading) -> None:
        pass

    def publish_cumulative(self, reading: CumulativeEnergyReading) -> None:
        pass

    def publish_interval(self, reading: IntervalEnergyReading) -> None:
        pass

    def close(self) -> None:
        pass


class MqttMeasurementPublisher:
    """Publish current readings only; disconnected readings are deliberately dropped."""

    def __init__(
        self,
        config: MqttConfig,
        *,
        client_factory: Callable[..., mqtt.Client] = mqtt.Client,
    ) -> None:
        self._config = config
        self._connected = False
        self._started = False
        self._closed = False
        self._client = client_factory(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.max_queued_messages_set(1)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        if config.username is not None:
            self._client.username_pw_set(config.username, config.password)
        self._client.will_set(
            self._status_topic,
            self._status_payload("offline"),
            qos=0,
            retain=True,
        )

    @property
    def _topic_base(self) -> str:
        return f"{self._config.topic_prefix}/{self._config.device_id}"

    @property
    def _status_topic(self) -> str:
        return f"{self._topic_base}/status"

    def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        try:
            self._client.connect_async(self._config.host, self._config.port, keepalive=60)
            self._client.loop_start()
        except Exception:
            logger.warning("MQTTクライアントを開始できませんでした", exc_info=True)

    def publish_instantaneous(self, reading: InstantaneousPowerReading) -> None:
        self._publish(
            "power",
            {
                "device_id": self._config.device_id,
                "measured_at": reading.measured_at.isoformat(),
                "net_power_w": reading.net_power_w,
            },
        )

    def publish_cumulative(self, reading: CumulativeEnergyReading) -> None:
        self._publish(
            "cumulative-energy",
            {
                "device_id": self._config.device_id,
                "metered_at": reading.metered_at.isoformat(),
                "received_at": reading.received_at.isoformat(),
                "cumulative_energy_import_kwh": reading.forward_total_kwh,
                "cumulative_energy_export_kwh": reading.reverse_total_kwh,
            },
        )

    def publish_interval(self, reading: IntervalEnergyReading) -> None:
        self._publish(
            "interval-energy",
            {
                "device_id": self._config.device_id,
                "start_at": reading.start_at.isoformat(),
                "end_at": reading.end_at.isoformat(),
                "import_energy_kwh": reading.import_energy_kwh,
                "export_energy_kwh": reading.export_energy_kwh,
                "quality_status": reading.quality_status,
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._connected:
            try:
                result = self._client.publish(
                    self._status_topic,
                    self._status_payload("offline"),
                    qos=0,
                    retain=True,
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    result.wait_for_publish(timeout=1.0)
            except Exception:
                logger.warning("MQTT offline statusを送信できませんでした", exc_info=True)
        try:
            self._client.loop_stop()
        except Exception:
            logger.warning("MQTTネットワークループを停止できませんでした", exc_info=True)
        try:
            self._client.disconnect()
        except Exception:
            logger.warning("MQTTクライアントを切断できませんでした", exc_info=True)
        finally:
            self._connected = False

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _connect_flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if getattr(reason_code, "is_failure", False) or reason_code != 0:
            logger.warning("MQTT接続に失敗しました reason=%s", reason_code)
            return
        was_connected = self._connected
        self._connected = True
        logger.info("MQTTへ%sしました", "再接続" if was_connected else "接続")
        self._publish(
            "status",
            {"device_id": self._config.device_id, "status": "online"},
            retain=True,
        )

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if self._connected and not self._closed:
            logger.warning("MQTT接続が切断されました reason=%s", reason_code)
        self._connected = False

    def _publish(self, suffix: str, payload: dict[str, object], *, retain: bool = False) -> None:
        if not self._connected:
            return
        try:
            encoded = json.dumps(_json_values(payload), ensure_ascii=False, allow_nan=False)
            result = self._client.publish(
                f"{self._topic_base}/{suffix}",
                encoded,
                qos=0,
                retain=retain,
            )
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.debug("MQTT publishを省略しました topic=%s rc=%s", suffix, result.rc)
        except Exception:
            logger.warning("MQTT publishに失敗しました topic=%s", suffix, exc_info=True)

    def _status_payload(self, status: str) -> str:
        return json.dumps(
            {"device_id": self._config.device_id, "status": status},
            ensure_ascii=False,
            allow_nan=False,
        )


def _json_values(value: object) -> object:
    """Convert finite Decimal values while preserving JSON null for None."""

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal must be finite")
        converted = float(value)
        if converted == float("inf") or converted == float("-inf"):
            raise ValueError("Decimal cannot be represented as a finite JSON number")
        return converted
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    return value


def create_measurement_publisher(config: MqttConfig) -> MeasurementPublisher:
    """Avoid creating an MQTT client at all unless explicitly enabled."""

    if not config.enabled:
        return NullMeasurementPublisher()
    return MqttMeasurementPublisher(config)
