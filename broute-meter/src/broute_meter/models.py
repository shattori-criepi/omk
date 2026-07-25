"""アプリケーション内で共有する型付きデータモデル。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from ipaddress import IPv6Address
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ActiveScanResult:
    """RS-WSUHA-PのEPANDESCから得たスマートメーター候補。"""

    channel: str
    pan_id: str
    address: str
    channel_page: str | None = None
    lqi: str | None = None
    pair_id: str | None = None
    raw_fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "raw_fields",
            MappingProxyType(dict(self.raw_fields)),
        )


@dataclass(frozen=True, slots=True)
class BRouteConnection:
    """PANA接続済みスマートメーターの接続情報。"""

    scan_result: ActiveScanResult
    smart_meter_ipv6: IPv6Address


@dataclass(frozen=True, slots=True)
class InstantaneousPowerReading:
    """系統接続点における正味の瞬時電力。正が買電、負が逆潮流。"""

    measured_at: datetime
    net_power_w: int


@dataclass(frozen=True, slots=True)
class CumulativeEnergyReading:
    """30分計量時刻における正方向・逆方向の累積電力量。"""

    metered_at: datetime
    received_at: datetime
    forward_raw: int
    reverse_raw: int | None
    forward_total_kwh: Decimal
    reverse_total_kwh: Decimal | None


@dataclass(frozen=True, slots=True)
class IntervalEnergyReading:
    """連続する定時積算値から算出した30分買電量・売電量。"""

    start_at: datetime
    end_at: datetime
    import_energy_kwh: Decimal
    export_energy_kwh: Decimal | None
    quality_status: str
