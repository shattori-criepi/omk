"""定時積算値から30分買電量・売電量を算出する。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from broute_meter.models import CumulativeEnergyReading, IntervalEnergyReading

EXPECTED_INTERVAL = timedelta(minutes=30)


class QualityStatus(StrEnum):
    """30分値の算出品質。"""

    NORMAL = "normal"
    MISSING_PREVIOUS = "missing_previous"
    TIME_GAP = "time_gap"
    NEGATIVE_DELTA = "negative_delta"
    REVERSE_NOT_SUPPORTED = "reverse_not_supported"


@dataclass(frozen=True, slots=True)
class IntervalCalculation:
    """品質判定と、生成可能な場合の30分値。"""

    quality_status: QualityStatus
    reading: IntervalEnergyReading | None


def calculate_interval_energy(
    previous: CumulativeEnergyReading | None,
    current: CumulativeEnergyReading,
) -> IntervalCalculation:
    """連続する積算値を検証し、均等配分せず30分差分を算出する。"""

    if previous is None:
        return IntervalCalculation(QualityStatus.MISSING_PREVIOUS, None)

    if current.metered_at - previous.metered_at != EXPECTED_INTERVAL:
        return IntervalCalculation(QualityStatus.TIME_GAP, None)

    import_energy = current.forward_total_kwh - previous.forward_total_kwh
    if import_energy < 0:
        return IntervalCalculation(QualityStatus.NEGATIVE_DELTA, None)

    if (
        previous.reverse_total_kwh is None
        or current.reverse_total_kwh is None
    ):
        status = QualityStatus.REVERSE_NOT_SUPPORTED
        return IntervalCalculation(
            status,
            IntervalEnergyReading(
                start_at=previous.metered_at,
                end_at=current.metered_at,
                import_energy_kwh=import_energy,
                export_energy_kwh=None,
                quality_status=status.value,
            ),
        )

    export_energy = (
        current.reverse_total_kwh - previous.reverse_total_kwh
    )
    if export_energy < 0:
        return IntervalCalculation(QualityStatus.NEGATIVE_DELTA, None)

    status = QualityStatus.NORMAL
    return IntervalCalculation(
        status,
        IntervalEnergyReading(
            start_at=previous.metered_at,
            end_at=current.metered_at,
            import_energy_kwh=import_energy,
            export_energy_kwh=export_energy,
            quality_status=status.value,
        ),
    )
