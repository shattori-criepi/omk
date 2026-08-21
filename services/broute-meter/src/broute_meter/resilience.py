"""連続通信失敗の追跡とメーター接続の再確立。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, TypeVar

from broute_meter.adapter import AdapterOperationCancelled
from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading

logger = logging.getLogger(__name__)
_Reading = TypeVar("_Reading")


class MeasurementUnavailableError(RuntimeError):
    """今回の計測は取得できず、欠測として次の時刻へ進む。"""


class MeasurementCancelledError(RuntimeError):
    """終了要求により意図的に計測を中断したため、欠測ではない。"""


class MeterReader(Protocol):
    """耐障害ラッパーが利用する計測クライアント境界。"""

    def get_instantaneous_power(self) -> InstantaneousPowerReading: ...

    def get_cumulative_energy(self) -> CumulativeEnergyReading: ...


class StopEvent(Protocol):
    """再接続待機を中断できる終了イベント。"""

    def wait(self, timeout: float | None = None) -> bool: ...


class MeterReconnect(Protocol):
    """再接続後の新しいメーター取得クライアントを返す。"""

    def __call__(self) -> MeterReader:
        """シリアルおよびPANA接続を再確立する。"""
        ...


class RecoveringMeterReader:
    """成功時に失敗数を戻し、閾値到達時に接続を再確立する。"""

    def __init__(
        self,
        meter: MeterReader,
        reconnect: MeterReconnect,
        *,
        recoverable_exceptions: tuple[type[Exception], ...],
        reconnect_after_consecutive_failures: int,
        reconnect_wait_seconds: float,
        stop_event: StopEvent,
    ) -> None:
        if reconnect_after_consecutive_failures < 1:
            raise ValueError("reconnect failure threshold must be at least one")
        if reconnect_wait_seconds < 0:
            raise ValueError("reconnect wait must not be negative")
        if not recoverable_exceptions:
            raise ValueError("at least one recoverable exception is required")
        self._meter = meter
        self._reconnect = reconnect
        self._recoverable_exceptions = recoverable_exceptions
        self._threshold = reconnect_after_consecutive_failures
        self._wait_seconds = reconnect_wait_seconds
        self._stop_event = stop_event
        self._consecutive_failures = 0

    def get_instantaneous_power(self) -> InstantaneousPowerReading:
        """瞬時電力を取得し、失敗時は欠測または再接続を行う。"""

        return self._execute("瞬時電力", self._meter.get_instantaneous_power)

    def get_cumulative_energy(self) -> CumulativeEnergyReading:
        """定時積算値を取得し、失敗時は欠測または再接続を行う。"""

        return self._execute("定時積算電力量", self._meter.get_cumulative_energy)

    def _execute(
        self,
        measurement_name: str,
        operation: Callable[[], _Reading],
    ) -> _Reading:
        try:
            reading = operation()
        except AdapterOperationCancelled as exc:
            raise MeasurementCancelledError(
                "終了要求により計測を中断しました。"
            ) from exc
        except self._recoverable_exceptions as exc:
            self._consecutive_failures += 1
            logger.warning(
                "%sの取得に失敗しました consecutive_failures=%d/%d",
                measurement_name,
                self._consecutive_failures,
                self._threshold,
            )
            if self._consecutive_failures < self._threshold:
                raise MeasurementUnavailableError(
                    f"{measurement_name}を取得できませんでした。"
                ) from exc
            return self._reconnect_and_retry(measurement_name, exc)

        self._consecutive_failures = 0
        return reading

    def _reconnect_and_retry(
        self,
        measurement_name: str,
        original_error: Exception,
    ) -> _Reading:
        logger.warning(
            "連続失敗が閾値に達したため再接続します wait_seconds=%s",
            self._wait_seconds,
        )
        if self._stop_event.wait(self._wait_seconds):
            raise MeasurementCancelledError(
                "終了要求により再接続を中止しました。"
            ) from original_error

        try:
            self._meter = self._reconnect()
            self._consecutive_failures = 0
            operation = (
                self._meter.get_instantaneous_power
                if measurement_name == "瞬時電力"
                else self._meter.get_cumulative_energy
            )
            reading = operation()
        except AdapterOperationCancelled as exc:
            raise MeasurementCancelledError(
                "終了要求により再接続後の計測を中断しました。"
            ) from exc
        except self._recoverable_exceptions as exc:
            self._consecutive_failures = 1
            raise MeasurementUnavailableError(
                f"再接続後も{measurement_name}を取得できませんでした。"
            ) from exc

        logger.info("再接続後の%s取得に成功しました", measurement_name)
        return reading
