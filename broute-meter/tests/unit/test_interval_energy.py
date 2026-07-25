"""30分買電・売電量算出の単体テスト。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from broute_meter.models import CumulativeEnergyReading
from broute_meter.processing import QualityStatus, calculate_interval_energy


def _reading(
    metered_at: datetime,
    forward: str,
    reverse: str | None,
) -> CumulativeEnergyReading:
    return CumulativeEnergyReading(
        metered_at=metered_at,
        received_at=metered_at,
        forward_raw=0,
        reverse_raw=0 if reverse is not None else None,
        forward_total_kwh=Decimal(forward),
        reverse_total_kwh=Decimal(reverse) if reverse is not None else None,
    )


@pytest.mark.parametrize(
    ("previous_values", "current_values", "expected_import", "expected_export"),
    [
        (("100.00", "20.00"), ("100.35", "20.12"), "0.35", "0.12"),
        (("100.00", "20.00"), ("100.35", "20.00"), "0.35", "0.00"),
        (("100.00", "20.00"), ("100.00", "20.12"), "0.00", "0.12"),
        (("0.1", "0.2"), ("0.3", "0.5"), "0.2", "0.3"),
    ],
)
def test_calculate_normal_interval_without_float_rounding(
    previous_values: tuple[str, str],
    current_values: tuple[str, str],
    expected_import: str,
    expected_export: str,
) -> None:
    start = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
    result = calculate_interval_energy(
        _reading(start, *previous_values),
        _reading(start + timedelta(minutes=30), *current_values),
    )

    assert result.quality_status is QualityStatus.NORMAL
    assert result.reading is not None
    assert result.reading.import_energy_kwh == Decimal(expected_import)
    assert result.reading.export_energy_kwh == Decimal(expected_export)
    assert result.reading.quality_status == "normal"


def test_missing_previous_does_not_generate_interval() -> None:
    current = _reading(datetime(2026, 7, 25, tzinfo=UTC), "1", "2")

    result = calculate_interval_energy(None, current)

    assert result.quality_status is QualityStatus.MISSING_PREVIOUS
    assert result.reading is None


@pytest.mark.parametrize("minutes", [0, 60, 90])
def test_non_30_minute_gap_does_not_generate_interval(minutes: int) -> None:
    start = datetime(2026, 7, 25, tzinfo=UTC)

    result = calculate_interval_energy(
        _reading(start, "1", "2"),
        _reading(start + timedelta(minutes=minutes), "2", "3"),
    )

    assert result.quality_status is QualityStatus.TIME_GAP
    assert result.reading is None


@pytest.mark.parametrize(
    ("previous_values", "current_values"),
    [
        (("2", "3"), ("1", "4")),
        (("2", "3"), ("3", "2")),
    ],
)
def test_negative_delta_does_not_generate_interval(
    previous_values: tuple[str, str],
    current_values: tuple[str, str],
) -> None:
    start = datetime(2026, 7, 25, tzinfo=UTC)

    result = calculate_interval_energy(
        _reading(start, *previous_values),
        _reading(start + timedelta(minutes=30), *current_values),
    )

    assert result.quality_status is QualityStatus.NEGATIVE_DELTA
    assert result.reading is None


def test_reverse_not_supported_keeps_only_valid_import_delta() -> None:
    start = datetime(2026, 7, 25, tzinfo=UTC)

    result = calculate_interval_energy(
        _reading(start, "1.0", None),
        _reading(start + timedelta(minutes=30), "1.4", None),
    )

    assert result.quality_status is QualityStatus.REVERSE_NOT_SUPPORTED
    assert result.reading is not None
    assert result.reading.import_energy_kwh == Decimal("0.4")
    assert result.reading.export_energy_kwh is None
