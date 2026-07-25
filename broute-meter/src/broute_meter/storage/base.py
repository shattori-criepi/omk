"""計測値保存先の抽象インターフェース。"""

from __future__ import annotations

from typing import Protocol

from broute_meter.models import (
    CumulativeEnergyReading,
    InstantaneousPowerReading,
    IntervalEnergyReading,
)


class StorageError(RuntimeError):
    """計測値を永続化できなかった。"""


class MeasurementStorage(Protocol):
    """計測処理が依存する保存先の最小契約。"""

    def save_instantaneous(self, reading: InstantaneousPowerReading) -> None:
        """瞬時電力を保存する。"""
        ...

    def save_cumulative(self, reading: CumulativeEnergyReading) -> bool:
        """未保存の計量時刻なら積算値を保存してTrueを返す。"""
        ...

    def latest_cumulative(self) -> CumulativeEnergyReading | None:
        """保存済みの最新積算値を返す。"""
        ...

    def save_interval(self, reading: IntervalEnergyReading) -> bool:
        """未保存の終了時刻なら30分値を保存してTrueを返す。"""
        ...

    def close(self) -> None:
        """保留中の書込みを完了し、保存先を閉じる。"""
        ...
