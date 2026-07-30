"""Presentation models and formatting for the dashboard."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.data.parquet_repository import EnergyTotals, LatestPower, LatestSen66, ParquetRepository

JST = ZoneInfo("Asia/Tokyo")
NORMAL_MAX_AGE_SECONDS = 6 * 60
DELAYED_MAX_AGE_SECONDS = 10 * 60


class FreshnessStatus(StrEnum):
    NORMAL = "normal"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class PowerDirection(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"
    NEUTRAL = "neutral"
    UNAVAILABLE = "unavailable"


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
    updated_at_iso: str
    freshness: FreshnessStatus


def get_display_view_model(repository: ParquetRepository, now: datetime | None = None) -> DisplayViewModel:
    """Build a display-ready model from the latest processed measurements."""
    current_time = _as_jst(now or datetime.now(JST))
    power = repository.latest_power()
    sen66 = repository.latest_sen66()
    totals = repository.today_energy_totals(current_time.date())

    power_freshness = freshness_for(power.measured_at if power else None, current_time)
    sen66_freshness = freshness_for(sen66.measured_at if sen66 else None, current_time)
    updated_at = oldest_available(power.measured_at if power else None, sen66.measured_at if sen66 else None)

    power_value, direction_label, direction = format_power(power)
    return DisplayViewModel(
        current_power_kw=power_value,
        power_direction=direction_label,
        power_flow=direction,
        purchased_today_kwh=f"{totals.import_energy_kwh:.1f}",
        sold_today_kwh=f"{totals.export_energy_kwh:.1f}",
        temperature_c=format_optional(sen66.temperature_c if sen66 else None, 1),
        humidity_percent=format_optional(sen66.relative_humidity_pct if sen66 else None, 0),
        co2_ppm=format_optional(sen66.co2_ppm if sen66 else None, 0),
        pm25_ug_m3=format_optional(sen66.pm2_5_ug_m3 if sen66 else None, 1),
        air_quality=air_quality(sen66),
        updated_at=updated_at.strftime("%Y/%m/%d %H:%M") if updated_at else "--",
        updated_at_iso=updated_at.isoformat() if updated_at else "",
        freshness=worst_freshness(power_freshness, sen66_freshness),
    )


def freshness_for(measured_at: datetime | None, now: datetime) -> FreshnessStatus:
    if measured_at is None:
        return FreshnessStatus.UNAVAILABLE
    age_seconds = max(0.0, (_as_jst(now) - _as_jst(measured_at)).total_seconds())
    if age_seconds <= NORMAL_MAX_AGE_SECONDS:
        return FreshnessStatus.NORMAL
    if age_seconds <= DELAYED_MAX_AGE_SECONDS:
        return FreshnessStatus.DELAYED
    return FreshnessStatus.STALE


def worst_freshness(*statuses: FreshnessStatus) -> FreshnessStatus:
    ranking = {status: index for index, status in enumerate(FreshnessStatus)}
    return max(statuses, key=ranking.__getitem__)


def format_power(power: LatestPower | None) -> tuple[str, str, PowerDirection]:
    if power is None:
        return "--", "データなし", PowerDirection.UNAVAILABLE
    if power.net_power_w > 0:
        return f"{abs(power.net_power_w) / 1000:.2f}", "買電", PowerDirection.PURCHASE
    if power.net_power_w < 0:
        return f"{abs(power.net_power_w) / 1000:.2f}", "売電", PowerDirection.SALE
    return "0.00", "収支なし", PowerDirection.NEUTRAL


def air_quality(sen66: LatestSen66 | None) -> str:
    if sen66 is None or sen66.co2_ppm is None or sen66.pm2_5_ug_m3 is None:
        return "不明"
    if sen66.co2_ppm <= 1000 and sen66.pm2_5_ug_m3 <= 15:
        return "良好"
    if sen66.co2_ppm <= 1500 and sen66.pm2_5_ug_m3 <= 35:
        return "注意"
    return "要確認"


def format_optional(value: float | None, decimals: int) -> str:
    return "--" if value is None else f"{value:.{decimals}f}"


def oldest_available(*timestamps: datetime | None) -> datetime | None:
    available = [_as_jst(timestamp) for timestamp in timestamps if timestamp is not None]
    return min(available) if available else None


def _as_jst(timestamp: datetime) -> datetime:
    return timestamp.replace(tzinfo=JST) if timestamp.tzinfo is None else timestamp.astimezone(JST)
