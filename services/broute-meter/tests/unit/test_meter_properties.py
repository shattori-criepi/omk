"""スマートメータープロパティ解析の単体テスト。"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest

from broute_meter.meter.properties import (
    MeterPropertyError,
    convert_cumulative_energy,
    parse_coefficient,
    parse_energy_unit,
    parse_instantaneous_power,
    parse_significant_digits,
    parse_timed_cumulative_energy,
)


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("000004E2", 1250),
        ("FFFFFCB8", -840),
        ("00000000", 0),
        ("80000000", -(2**31)),
        ("7FFFFFFF", 2**31 - 1),
    ],
)
def test_parse_instantaneous_power_as_signed_big_endian(
    encoded: str,
    expected: int,
) -> None:
    assert parse_instantaneous_power(bytes.fromhex(encoded)) == expected


@pytest.mark.parametrize("edt", [b"", b"\x00", b"\x00" * 3, b"\x00" * 5])
def test_reject_invalid_instantaneous_power_length(edt: bytes) -> None:
    with pytest.raises(MeterPropertyError):
        parse_instantaneous_power(edt)


def test_parse_coefficient_and_significant_digits() -> None:
    assert parse_coefficient(bytes.fromhex("0000000A")) == 10
    assert parse_significant_digits(b"\x08") == 8


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x00, "1"),
        (0x01, "0.1"),
        (0x02, "0.01"),
        (0x03, "0.001"),
        (0x04, "0.0001"),
        (0x0A, "10"),
        (0x0B, "100"),
        (0x0C, "1000"),
        (0x0D, "10000"),
    ],
)
def test_parse_all_specified_energy_units(code: int, expected: str) -> None:
    assert parse_energy_unit(bytes((code,))) == Decimal(expected)


def test_parse_timed_cumulative_energy() -> None:
    metered_at, raw = parse_timed_cumulative_energy(
        bytes.fromhex("07EA0719121E000012D687")
    )

    assert metered_at.isoformat() == "2026-07-25T18:30:00+09:00"
    assert metered_at.utcoffset() == timedelta(hours=9)
    assert raw == 1_234_567


def test_parse_timed_cumulative_energy_no_measurement() -> None:
    metered_at, raw = parse_timed_cumulative_energy(
        bytes.fromhex("07EA0719120000FFFFFFFE")
    )

    assert metered_at.isoformat() == "2026-07-25T18:00:00+09:00"
    assert raw is None


def test_convert_cumulative_energy_uses_decimal_exactly() -> None:
    assert convert_cumulative_energy(
        12_345_678,
        coefficient=10,
        unit_kwh=Decimal("0.001"),
    ) == Decimal("123456.780")


@pytest.mark.parametrize(
    "call",
    [
        lambda: parse_coefficient(b"\x00"),
        lambda: parse_coefficient(bytes.fromhex("00100000")),
        lambda: parse_significant_digits(b"\x00"),
        lambda: parse_significant_digits(b"\x09"),
        lambda: parse_energy_unit(b"\x05"),
        lambda: parse_timed_cumulative_energy(b"\x00" * 10),
        lambda: parse_timed_cumulative_energy(
            bytes.fromhex("07EA0D0112000000000001")
        ),
        lambda: parse_timed_cumulative_energy(
            bytes.fromhex("07EA071912000005F5E100")
        ),
    ],
)
def test_reject_invalid_cumulative_property(call: Callable[[], object]) -> None:
    with pytest.raises(MeterPropertyError):
        call()
