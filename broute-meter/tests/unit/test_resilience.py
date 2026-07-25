"""連続失敗と再接続処理の単体テスト。"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import pytest

from broute_meter.models import InstantaneousPowerReading
from broute_meter.resilience import (
    MeasurementUnavailableError,
    RecoveringMeterReader,
)


class CommunicationFailure(RuntimeError):
    pass


class ScriptedMeter:
    def __init__(self, outcomes: list[int | Exception]) -> None:
        self.outcomes = deque(outcomes)

    def get_instantaneous_power(self) -> InstantaneousPowerReading:
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return InstantaneousPowerReading(
            datetime(2026, 7, 25, tzinfo=UTC),
            outcome,
        )

    def get_cumulative_energy(self):
        raise AssertionError("このテストでは使用しません")


class StopEvent:
    def __init__(self, stopped: bool = False) -> None:
        self.stopped = stopped
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return self.stopped


def _recovering(
    meter: ScriptedMeter,
    reconnect,
    stop_event: StopEvent,
    *,
    threshold: int = 2,
) -> RecoveringMeterReader:
    return RecoveringMeterReader(
        meter,
        reconnect,
        recoverable_exceptions=(CommunicationFailure,),
        reconnect_after_consecutive_failures=threshold,
        reconnect_wait_seconds=30,
        stop_event=stop_event,
    )


def test_success_resets_consecutive_failure_count() -> None:
    meter = ScriptedMeter(
        [
            CommunicationFailure("first"),
            100,
            CommunicationFailure("after success"),
        ]
    )
    reconnect_calls = 0

    def reconnect() -> ScriptedMeter:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return ScriptedMeter([200])

    recovering = _recovering(meter, reconnect, StopEvent())

    with pytest.raises(MeasurementUnavailableError):
        recovering.get_instantaneous_power()
    assert recovering.get_instantaneous_power().net_power_w == 100
    with pytest.raises(MeasurementUnavailableError):
        recovering.get_instantaneous_power()
    assert reconnect_calls == 0


def test_threshold_reopens_connection_and_retries_measurement() -> None:
    meter = ScriptedMeter(
        [
            CommunicationFailure("first"),
            CommunicationFailure("second"),
        ]
    )
    reconnect_calls = 0

    def reconnect() -> ScriptedMeter:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return ScriptedMeter([321])

    stop_event = StopEvent()
    recovering = _recovering(meter, reconnect, stop_event)

    with pytest.raises(MeasurementUnavailableError):
        recovering.get_instantaneous_power()
    reading = recovering.get_instantaneous_power()

    assert reading.net_power_w == 321
    assert reconnect_calls == 1
    assert stop_event.waits == [30]


def test_shutdown_interrupts_reconnect_wait() -> None:
    meter = ScriptedMeter([CommunicationFailure("stop")])
    reconnect_calls = 0

    def reconnect() -> ScriptedMeter:
        nonlocal reconnect_calls
        reconnect_calls += 1
        return ScriptedMeter([1])

    recovering = _recovering(
        meter,
        reconnect,
        StopEvent(stopped=True),
        threshold=1,
    )

    with pytest.raises(MeasurementUnavailableError, match="終了要求"):
        recovering.get_instantaneous_power()
    assert reconnect_calls == 0
