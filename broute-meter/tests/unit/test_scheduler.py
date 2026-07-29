"""時刻基準スケジューラーの単体テスト。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from broute_meter.models import (
    CumulativeEnergyReading,
    InstantaneousPowerReading,
    IntervalEnergyReading,
)
from broute_meter.scheduler import MeasurementScheduler, next_aligned_timestamp


class MutableClock:
    def __init__(self, timestamp: float) -> None:
        self.timestamp = timestamp

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=UTC)


class VirtualStopEvent:
    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, seconds: float) -> bool:
        self.clock.timestamp += seconds
        return self.stopped

    def set(self) -> None:
        self.stopped = True


class RecordingStorage:
    def __init__(self) -> None:
        self.instantaneous: list[InstantaneousPowerReading] = []
        self.cumulative: list[CumulativeEnergyReading] = []
        self.intervals: list[IntervalEnergyReading] = []

    def save_instantaneous(self, reading: InstantaneousPowerReading) -> None:
        self.instantaneous.append(reading)

    def save_cumulative(self, reading: CumulativeEnergyReading) -> bool:
        self.cumulative.append(reading)
        return True

    def latest_cumulative(self) -> CumulativeEnergyReading | None:
        return self.cumulative[-1] if self.cumulative else None

    def save_interval(self, reading: IntervalEnergyReading) -> bool:
        self.intervals.append(reading)
        return True

    def close(self) -> None:
        pass


class DelayedMeter:
    def __init__(
        self,
        clock: MutableClock,
        stop_event: VirtualStopEvent,
    ) -> None:
        self.clock = clock
        self.stop_event = stop_event
        self.request_times: list[float] = []
        self.cumulative_request_times: list[float] = []

    def get_instantaneous_power(self) -> InstantaneousPowerReading:
        self.request_times.append(self.clock.timestamp)
        reading = InstantaneousPowerReading(self.clock.now(), 100)
        if len(self.request_times) == 1:
            self.clock.timestamp += 35
        else:
            self.stop_event.set()
        return reading

    def get_cumulative_energy(self) -> CumulativeEnergyReading:
        self.cumulative_request_times.append(self.clock.timestamp)
        now = self.clock.now()
        return CumulativeEnergyReading(
            now,
            now,
            1,
            1,
            Decimal("1"),
            Decimal("1"),
        )


def test_next_aligned_timestamp_uses_wall_clock_boundaries() -> None:
    assert next_aligned_timestamp(100, 10) == 100
    assert next_aligned_timestamp(101, 10) == 110
    assert next_aligned_timestamp(109.9, 10) == 110


def test_scheduler_skips_missed_instants_instead_of_catching_up() -> None:
    clock = MutableClock(1)
    stop_event = VirtualStopEvent(clock)
    meter = DelayedMeter(clock, stop_event)
    storage = RecordingStorage()
    scheduler = MeasurementScheduler(
        meter,
        storage,
        instantaneous_interval_seconds=10,
        cumulative_fetch_delay_seconds=5,
        now=clock.now,
        stop_event=stop_event,
    )

    scheduler.run()

    assert meter.request_times == [10, 50]
    assert meter.cumulative_request_times == [1]
    assert len(storage.instantaneous) == 2


def test_scheduler_saves_both_measurements_at_shared_boundary() -> None:
    clock = MutableClock(0)
    stop_event = VirtualStopEvent(clock)
    storage = RecordingStorage()

    class Meter:
        def get_instantaneous_power(self) -> InstantaneousPowerReading:
            return InstantaneousPowerReading(clock.now(), -1)

        def get_cumulative_energy(self) -> CumulativeEnergyReading:
            stop_event.set()
            now = clock.now()
            return CumulativeEnergyReading(
                now,
                now,
                10,
                20,
                Decimal("1.0"),
                Decimal("2.0"),
            )

    MeasurementScheduler(
        Meter(),
        storage,
        instantaneous_interval_seconds=10,
        cumulative_fetch_delay_seconds=5,
        now=clock.now,
        stop_event=stop_event,
    ).run()

    assert len(storage.instantaneous) == 1
    assert len(storage.cumulative) == 1
    assert storage.intervals == []


def test_scheduler_uses_cumulative_restored_from_storage() -> None:
    clock = MutableClock(1800)
    stop_event = VirtualStopEvent(clock)
    storage = RecordingStorage()
    previous_time = datetime.fromtimestamp(0, tz=UTC)
    storage.cumulative.append(
        CumulativeEnergyReading(
            previous_time,
            previous_time,
            100,
            200,
            Decimal("10.0"),
            Decimal("20.0"),
        )
    )

    class Meter:
        def get_instantaneous_power(self) -> InstantaneousPowerReading:
            return InstantaneousPowerReading(clock.now(), 1)

        def get_cumulative_energy(self) -> CumulativeEnergyReading:
            stop_event.set()
            now = clock.now()
            return CumulativeEnergyReading(
                now,
                now,
                110,
                205,
                Decimal("10.5"),
                Decimal("20.2"),
            )

    MeasurementScheduler(
        Meter(),
        storage,
        instantaneous_interval_seconds=10,
        cumulative_fetch_delay_seconds=5,
        now=clock.now,
        stop_event=stop_event,
    ).run()

    assert len(storage.intervals) == 1
    assert storage.intervals[0].import_energy_kwh == Decimal("0.5")
    assert storage.intervals[0].export_energy_kwh == Decimal("0.2")


def test_cumulative_is_requested_at_startup_and_after_half_hour_boundary() -> None:
    clock = MutableClock(1_799)
    stop_event = VirtualStopEvent(clock)
    storage = RecordingStorage()

    class Meter:
        def __init__(self) -> None:
            self.cumulative_times: list[float] = []

        def get_instantaneous_power(self) -> InstantaneousPowerReading:
            if clock.timestamp >= 1_810:
                stop_event.set()
            return InstantaneousPowerReading(clock.now(), 1)

        def get_cumulative_energy(self) -> CumulativeEnergyReading:
            self.cumulative_times.append(clock.timestamp)
            now = clock.now()
            return CumulativeEnergyReading(
                now,
                now,
                1,
                1,
                Decimal("1"),
                Decimal("1"),
            )

    meter = Meter()
    MeasurementScheduler(
        meter,
        storage,
        instantaneous_interval_seconds=10,
        cumulative_fetch_delay_seconds=5,
        now=clock.now,
        stop_event=stop_event,
    ).run()

    assert meter.cumulative_times == [1_799, 1_805]
