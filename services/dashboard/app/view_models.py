"""View models and temporary data providers for dashboard pages."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo


class FreshnessStatus(StrEnum):
    """How current the displayed measurement data is."""

    NORMAL = "normal"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class PowerDirection(StrEnum):
    """Direction of the grid power flow."""

    PURCHASE = "purchase"
    SALE = "sale"


@dataclass(frozen=True)
class DisplayViewModel:
    current_power_kw: str
    power_direction: str
    power_flow: PowerDirection
    purchased_today_kwh: str
    sold_today_kwh: str
    temperature_c: str
    humidity_percent: str
    co2_ppm: str
    pm25_ug_m3: str
    air_quality: str
    updated_at: str
    freshness: FreshnessStatus


def get_display_view_model() -> DisplayViewModel:
    """Return temporary display data until real data sources are connected."""
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    return DisplayViewModel(
        current_power_kw="1.24",
        power_direction="買電",
        power_flow=PowerDirection.PURCHASE,
        purchased_today_kwh="4.8",
        sold_today_kwh="8.2",
        temperature_c="26.4",
        humidity_percent="48",
        co2_ppm="612",
        pm25_ug_m3="5.3",
        air_quality="良好",
        updated_at=now.strftime("%Y/%m/%d %H:%M"),
        freshness=FreshnessStatus.NORMAL,
    )
