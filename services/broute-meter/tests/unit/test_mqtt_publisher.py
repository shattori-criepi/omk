"""Unit tests for best-effort MQTT measurement publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from broute_meter.config import MqttConfig
from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading
from broute_meter.mqtt.publisher import MqttMeasurementPublisher


class FakePublishResult:
    def __init__(self, rc: int = 0, wait_error: Exception | None = None) -> None:
        self.rc = rc
        self.wait_error = wait_error
        self.wait_timeouts: list[float] = []

    def wait_for_publish(self, timeout: float) -> None:
        self.wait_timeouts.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error


class FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.on_connect: object = None
        self.on_disconnect: object = None
        self.published: list[tuple[str, str, int, bool]] = []
        self.will: tuple[str, str, int, bool] | None = None
        self.connected_to: tuple[str, int, int] | None = None
        self.loop_started = False
        self.disconnected = False
        self.max_queued_messages: int | None = None
        self.publish_result = FakePublishResult()

    def reconnect_delay_set(self, **_kwargs: object) -> None:
        pass

    def max_queued_messages_set(self, queue_size: int) -> None:
        self.max_queued_messages = queue_size

    def username_pw_set(self, _username: str, _password: str | None) -> None:
        pass

    def will_set(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.will = (topic, payload, qos, retain)

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.connected_to = (host, port, keepalive)

    def loop_start(self) -> None:
        self.loop_started = True

    def publish(self, topic: str, payload: str, qos: int, retain: bool) -> FakePublishResult:
        self.published.append((topic, payload, qos, retain))
        return self.publish_result

    def loop_stop(self) -> None:
        self.loop_started = False

    def disconnect(self) -> None:
        self.disconnected = True


def _publisher() -> tuple[MqttMeasurementPublisher, FakeClient]:
    client = FakeClient()
    publisher = MqttMeasurementPublisher(
        MqttConfig(enabled=True),
        client_factory=lambda **_kwargs: client,
    )
    return publisher, client


def test_start_configures_will_and_publishes_online_after_connect() -> None:
    publisher, client = _publisher()

    publisher.start()
    assert client.connected_to == ("127.0.0.1", 1883, 60)
    assert client.will == (
        "omk/broute-001/status",
        '{"device_id": "broute-001", "status": "offline"}',
        0,
        True,
    )
    assert client.max_queued_messages == 1

    publisher._on_connect(client, None, None, 0, None)

    assert client.published == [
        (
            "omk/broute-001/status",
            '{"device_id": "broute-001", "status": "online"}',
            0,
            True,
        )
    ]


def test_measurements_use_expected_topics_standard_json_and_no_queue_when_disconnected() -> None:
    publisher, client = _publisher()
    now = datetime(2026, 7, 30, 11, 30, tzinfo=UTC)

    publisher.publish_instantaneous(InstantaneousPowerReading(now, -3581))
    assert client.published == []

    publisher._on_connect(client, None, None, 0, None)
    publisher.publish_instantaneous(InstantaneousPowerReading(now, -3581))
    publisher.publish_cumulative(
        CumulativeEnergyReading(now, now, 1, None, Decimal("1234.5"), None)
    )

    power = client.published[1]
    assert power[:1] == ("omk/broute-001/power",)
    assert power[2:] == (0, False)
    assert json.loads(power[1]) == {
        "device_id": "broute-001",
        "measured_at": now.isoformat(),
        "net_power_w": -3581,
    }
    cumulative = json.loads(client.published[2][1])
    assert cumulative["cumulative_energy_import_kwh"] == 1234.5
    assert cumulative["cumulative_energy_export_kwh"] is None


def test_close_publishes_offline_then_stops_loop_and_disconnects() -> None:
    publisher, client = _publisher()
    publisher._on_connect(client, None, None, 0, None)

    publisher.close()

    assert json.loads(client.published[-1][1])["status"] == "offline"
    assert client.published[-1][2:] == (0, True)
    assert client.publish_result.wait_timeouts == [1.0]
    assert not client.loop_started
    assert client.disconnected


def test_close_does_not_wait_when_offline_publish_fails() -> None:
    publisher, client = _publisher()
    publisher._on_connect(client, None, None, 0, None)
    client.publish_result = FakePublishResult(rc=1)

    publisher.close()

    assert client.publish_result.wait_timeouts == []
    assert client.disconnected


def test_close_stops_and_disconnects_when_offline_wait_fails() -> None:
    publisher, client = _publisher()
    publisher._on_connect(client, None, None, 0, None)
    client.publish_result = FakePublishResult(wait_error=RuntimeError("wait failure"))

    publisher.close()

    assert client.publish_result.wait_timeouts == [1.0]
    assert not client.loop_started
    assert client.disconnected


def test_close_while_disconnected_does_not_publish_offline() -> None:
    publisher, client = _publisher()

    publisher.close()

    assert client.published == []
    assert client.publish_result.wait_timeouts == []
    assert client.disconnected


def test_non_finite_decimal_is_dropped_without_raising() -> None:
    publisher, client = _publisher()
    now = datetime(2026, 7, 30, 11, 30, tzinfo=UTC)
    publisher._on_connect(client, None, None, 0, None)

    publisher.publish_cumulative(
        CumulativeEnergyReading(now, now, 1, None, Decimal("NaN"), None)
    )

    # The first message is status=online; invalid JSON payload is not sent.
    assert len(client.published) == 1
