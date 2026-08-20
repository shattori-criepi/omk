"""スマートメーター固有EDTの解析。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

COEFFICIENT_EPC = 0xD3
SIGNIFICANT_DIGITS_EPC = 0xD7
ENERGY_UNIT_EPC = 0xE1
INSTANTANEOUS_POWER_EPC = 0xE7
TIMED_FORWARD_ENERGY_EPC = 0xEA
TIMED_REVERSE_ENERGY_EPC = 0xEB

NO_MEASUREMENT_RAW = 0xFFFFFFFE
MAX_CUMULATIVE_RAW = 99_999_999
JST = timezone(timedelta(hours=9), name="JST")

_ENERGY_UNITS_KWH = {
    0x00: Decimal("1"),
    0x01: Decimal("0.1"),
    0x02: Decimal("0.01"),
    0x03: Decimal("0.001"),
    0x04: Decimal("0.0001"),
    0x0A: Decimal("10"),
    0x0B: Decimal("100"),
    0x0C: Decimal("1000"),
    0x0D: Decimal("10000"),
}


class MeterPropertyError(ValueError):
    """スマートメータープロパティ値が不正。"""


def parse_instantaneous_power(edt: bytes) -> int:
    """E7の4バイト符号付き整数をW単位で返す。"""

    if len(edt) != 4:
        raise MeterPropertyError("E7のEDTは4バイトである必要があります。")
    return int.from_bytes(edt, byteorder="big", signed=True)


def parse_coefficient(edt: bytes) -> int:
    """D3の4バイト係数を返す。"""

    if len(edt) != 4:
        raise MeterPropertyError("D3のEDTは4バイトである必要があります。")
    coefficient = int.from_bytes(edt, byteorder="big")
    if coefficient > 999_999:
        raise MeterPropertyError("D3の係数が仕様の値域を超えています。")
    return coefficient


def parse_significant_digits(edt: bytes) -> int:
    """D7の積算電力量有効桁数（1～8）を返す。"""

    if len(edt) != 1 or not 1 <= edt[0] <= 8:
        raise MeterPropertyError("D7は1バイトの1～8である必要があります。")
    return edt[0]


def parse_energy_unit(edt: bytes) -> Decimal:
    """E1の単位コードを1生値当たりのkWh倍率として返す。"""

    if len(edt) != 1:
        raise MeterPropertyError("E1のEDTは1バイトである必要があります。")
    try:
        return _ENERGY_UNITS_KWH[edt[0]]
    except KeyError as exc:
        raise MeterPropertyError(
            f"E1の単位コードが仕様にありません: 0x{edt[0]:02X}"
        ) from exc


def parse_timed_cumulative_energy(edt: bytes) -> tuple[datetime, int | None]:
    """EA/EBの計量時刻と積算生値を返す。データなしはNoneとする。"""

    if len(edt) != 11:
        raise MeterPropertyError("EA/EBのEDTは11バイトである必要があります。")

    year = int.from_bytes(edt[0:2], byteorder="big")
    try:
        metered_at = datetime(
            year,
            edt[2],
            edt[3],
            edt[4],
            edt[5],
            edt[6],
            tzinfo=JST,
        )
    except ValueError as exc:
        raise MeterPropertyError("EA/EBの計量日時が不正です。") from exc

    raw = int.from_bytes(edt[7:11], byteorder="big")
    if raw == NO_MEASUREMENT_RAW:
        return metered_at, None
    if raw > MAX_CUMULATIVE_RAW:
        raise MeterPropertyError("EA/EBの積算生値が仕様の値域を超えています。")
    return metered_at, raw


def convert_cumulative_energy(
    raw: int,
    *,
    coefficient: int,
    unit_kwh: Decimal,
) -> Decimal:
    """積算生値へD3係数とE1単位を適用し、kWhへ換算する。"""

    if not 0 <= raw <= MAX_CUMULATIVE_RAW:
        raise MeterPropertyError("積算生値が仕様の値域外です。")
    if not 0 <= coefficient <= 999_999:
        raise MeterPropertyError("係数が仕様の値域外です。")
    return Decimal(raw) * Decimal(coefficient) * unit_kwh
