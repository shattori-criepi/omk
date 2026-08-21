"""時刻基準の計測スケジューラー。"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading
from broute_meter.mqtt import MeasurementPublisher, NullMeasurementPublisher
from broute_meter.processing import QualityStatus, calculate_interval_energy
from broute_meter.resilience import (
    MeasurementCancelledError,
    MeasurementUnavailableError,
)
from broute_meter.storage import MeasurementStorage

logger = logging.getLogger(__name__)


class MeterReader(Protocol):
    """スケジューラーが利用するスマートメーター取得境界。"""

    def get_instantaneous_power(self) -> InstantaneousPowerReading:
        """瞬時電力を1件取得する。"""
        ...

    def get_cumulative_energy(self) -> CumulativeEnergyReading:
        """最新の定時積算電力量を1件取得する。"""
        ...


class StopEvent(Protocol):
    """終了通知と割込み可能な待機に必要なイベント境界。"""

    def is_set(self) -> bool:
        """終了要求済みならTrueを返す。"""
        ...

    def wait(self, timeout: float | None = None) -> bool:
        """終了通知またはタイムアウトまで待機する。"""
        ...

    def set(self) -> None:
        """終了を要求する。"""
        ...


def next_aligned_timestamp(timestamp: float, interval_seconds: int) -> float:
    """現在値以上となる次のUNIX時刻基準境界を返す。"""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than zero")
    quotient = timestamp / interval_seconds
    if quotient == math.floor(quotient):
        return timestamp
    return math.ceil(quotient) * interval_seconds


class MeasurementScheduler:
    """E7と定時積算値を異なる時刻基準で直列に取得する。"""

    def __init__(
        self,
        meter: MeterReader,
        storage: MeasurementStorage,
        publisher: MeasurementPublisher | None = None,
        *,
        instantaneous_interval_seconds: int,
        cumulative_fetch_delay_seconds: int,
        now: Callable[[], datetime] | None = None,
        stop_event: StopEvent | None = None,
    ) -> None:
        self._meter = meter
        self._storage = storage
        self._publisher = publisher or NullMeasurementPublisher()
        self._instant_interval = instantaneous_interval_seconds
        self._cumulative_delay = cumulative_fetch_delay_seconds
        self._now = now or (lambda: datetime.now().astimezone())
        self._stop_event = stop_event or threading.Event()
        self._previous_cumulative = storage.latest_cumulative()

    @property
    def stop_event(self) -> StopEvent:
        """終了要求に使用するイベントを返す。"""

        return self._stop_event

    def stop(self) -> None:
        """新しい計測要求を開始しないよう通知する。"""

        self._stop_event.set()

    def run(self) -> None:
        """終了通知まで、時刻基準の2種類の計測を実行する。"""

        initial = self._now().timestamp()
        next_instant = next_aligned_timestamp(initial, self._instant_interval)
        startup_cumulative_pending = True
        next_cumulative = _next_cumulative_timestamp(
            initial,
            self._cumulative_delay,
        )

        while not self._stop_event.is_set():
            current = self._now().timestamp()
            cumulative_due = current if startup_cumulative_pending else next_cumulative
            wait_seconds = min(next_instant, cumulative_due) - current
            if wait_seconds > 0 and self._stop_event.wait(wait_seconds):
                break
            if self._stop_event.is_set():
                break

            current = self._now().timestamp()
            if current >= next_instant:
                try:
                    self._measure_instantaneous()
                except MeasurementCancelledError:
                    return
                except MeasurementUnavailableError as exc:
                    logger.warning("瞬時電力を欠測として記録します: %s", exc)
                finally:
                    next_instant = _strictly_next_aligned_timestamp(
                        self._now().timestamp(),
                        self._instant_interval,
                    )
            if self._stop_event.is_set():
                break
            current = self._now().timestamp()
            if startup_cumulative_pending or current >= next_cumulative:
                try:
                    self._measure_cumulative()
                except MeasurementCancelledError:
                    return
                except MeasurementUnavailableError as exc:
                    logger.warning(
                        "定時積算電力量を欠測として記録します: %s",
                        exc,
                    )
                finally:
                    startup_cumulative_pending = False
                    next_cumulative = _next_cumulative_timestamp(
                        self._now().timestamp(),
                        self._cumulative_delay,
                    )

    def _measure_instantaneous(self) -> None:
        reading = self._meter.get_instantaneous_power()
        self._storage.save_instantaneous(reading)
        self._publish_safely("publish_instantaneous", reading)
        logger.info(
            "瞬時電力を保存しました measured_at=%s net_power_w=%d",
            reading.measured_at.isoformat(),
            reading.net_power_w,
        )

    def _measure_cumulative(self) -> None:
        reading = self._meter.get_cumulative_energy()
        if self._storage.save_cumulative(reading):
            self._publish_safely("publish_cumulative", reading)
            logger.info(
                "定時積算電力量を保存しました metered_at=%s",
                reading.metered_at.isoformat(),
            )
            calculation = calculate_interval_energy(
                self._previous_cumulative,
                reading,
            )
            if calculation.reading is not None:
                if self._storage.save_interval(calculation.reading):
                    self._publish_safely("publish_interval", calculation.reading)
                    logger.info(
                        "30分買電・売電量を保存しました start_at=%s end_at=%s "
                        "quality_status=%s",
                        calculation.reading.start_at.isoformat(),
                        calculation.reading.end_at.isoformat(),
                        calculation.quality_status.value,
                    )
            elif calculation.quality_status is QualityStatus.MISSING_PREVIOUS:
                logger.warning(
                    "前回積算値がないため30分値を生成しません metered_at=%s",
                    reading.metered_at.isoformat(),
                )
            elif calculation.quality_status is QualityStatus.TIME_GAP:
                logger.warning(
                    "積算値の時刻差が30分ではないため30分値を生成しません "
                    "previous=%s current=%s",
                    (
                        self._previous_cumulative.metered_at.isoformat()
                        if self._previous_cumulative is not None
                        else ""
                    ),
                    reading.metered_at.isoformat(),
                )
            else:
                logger.warning(
                    "積算値が減少したため30分値を生成しません "
                    "previous=%s current=%s",
                    (
                        self._previous_cumulative.metered_at.isoformat()
                        if self._previous_cumulative is not None
                        else ""
                    ),
                    reading.metered_at.isoformat(),
                )
            self._previous_cumulative = reading
        else:
            logger.debug(
                "同一計量時刻の積算値を保存済みのためスキップしました metered_at=%s",
                reading.metered_at.isoformat(),
            )

    def _publish_safely(self, method_name: str, reading: object) -> None:
        """Do not let an optional real-time sink interrupt durable storage."""

        try:
            getattr(self._publisher, method_name)(reading)
        except Exception:
            logger.warning("MQTT publishに失敗しました。計測を継続します", exc_info=True)


def _strictly_next_aligned_timestamp(
    timestamp: float,
    interval_seconds: int,
) -> float:
    return (math.floor(timestamp / interval_seconds) + 1) * interval_seconds


def _next_cumulative_timestamp(
    timestamp: float,
    delay_seconds: int,
) -> float:
    """次の毎時00分・30分の遅延後時刻を返す。"""

    if not 1 <= delay_seconds < 1800:
        raise ValueError("cumulative delay must be between 1 and 1799 seconds")
    boundary_seconds = 30 * 60
    next_boundary = (math.floor(timestamp / boundary_seconds) + 1) * boundary_seconds
    return next_boundary + delay_seconds
