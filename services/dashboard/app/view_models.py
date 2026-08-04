"""Presentation models and formatting for the dashboard."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.data.parquet_repository import (
    LatestIchijoPowerFlow,
    LatestPower,
    LatestSen66,
    ParquetRepository,
)

JST = ZoneInfo("Asia/Tokyo")
NORMAL_MAX_AGE_SECONDS = 6 * 60
DELAYED_MAX_AGE_SECONDS = 10 * 60


class FreshnessStatus(StrEnum):
    NORMAL = "normal"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"


class PowerDirection(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"
    NEUTRAL = "neutral"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DisplayViewModel:
    current_power_kw: str
    current_power_label: str
    power_direction: str
    power_flow: PowerDirection
    has_ichijo_power_flow: bool
    pv_power_kw: str
    battery_soc_percent: str
    battery_power_label: str
    battery_power_kw: str
    grid_flow_label: str
    grid_flow_kw: str
    grid_flow: PowerDirection
    purchased_today_kwh: str
    sold_today_kwh: str
    temperature_c: str
    humidity_percent: str
    co2_ppm: str
    pm25_ug_m3: str
    voc_index: str
    updated_at: str
    updated_at_iso: str
    freshness: FreshnessStatus


def get_display_view_model(repository: ParquetRepository, now: datetime | None = None) -> DisplayViewModel:
    """Build a display-ready model from the latest processed measurements."""
    current_time = _as_jst(now or datetime.now(JST))
    power = repository.latest_power()
    sen66 = repository.latest_sen66()
    ichijo = repository.latest_ichijo_power_flow()
    totals = repository.today_energy_totals(current_time.date())

    power_freshness = freshness_for(power.measured_at if power else None, current_time)
    sen66_freshness = freshness_for(sen66.measured_at if sen66 else None, current_time)
    ichijo_freshness = (
        freshness_for(ichijo.measured_at, current_time)
        if ichijo is not None
        else None
    )
    active_ichijo = (
        ichijo
        if ichijo_freshness is not None
        and ichijo_freshness != FreshnessStatus.UNAVAILABLE
        else None
    )

    updated_at = oldest_available(
        power.measured_at if power else None,
        sen66.measured_at if sen66 else None,
        active_ichijo.measured_at if active_ichijo else None,
    )

    if active_ichijo is not None:
        power_value = f"{active_ichijo.load_power_w / 1000:.2f}"
        direction_label = ""
        direction = PowerDirection.NEUTRAL
        current_power_label = "現在の消費電力"
    else:
        power_value, direction_label, direction = format_power(power)
        current_power_label = power_label(direction)

    battery_label, battery_power = format_battery_power(active_ichijo)
    grid_label, grid_power, grid_direction = format_grid_flow(active_ichijo)

    return DisplayViewModel(
        current_power_kw=power_value,
        current_power_label=current_power_label,
        power_direction=direction_label,
        power_flow=direction,
        has_ichijo_power_flow=active_ichijo is not None,
        pv_power_kw=format_optional(
            active_ichijo.pv_power_w / 1000 if active_ichijo else None,
            2,
        ),
        battery_soc_percent=format_optional(
            active_ichijo.battery_soc_percent if active_ichijo else None,
            0,
        ),
        battery_power_label=battery_label,
        battery_power_kw=battery_power,
        grid_flow_label=grid_label,
        grid_flow_kw=grid_power,
        grid_flow=grid_direction,
        purchased_today_kwh=f"{totals.import_energy_kwh:.1f}",
        sold_today_kwh=f"{totals.export_energy_kwh:.1f}",
        temperature_c=format_optional(sen66.temperature_c if sen66 else None, 1),
        humidity_percent=format_optional(sen66.relative_humidity_pct if sen66 else None, 0),
        co2_ppm=format_optional(sen66.co2_ppm if sen66 else None, 0),
        pm25_ug_m3=format_optional(sen66.pm2_5_ug_m3 if sen66 else None, 1),
        voc_index=format_optional(sen66.voc_index if sen66 else None, 0),
        updated_at=updated_at.strftime("%Y/%m/%d %H:%M:%S") if updated_at else "--",
        updated_at_iso=updated_at.isoformat() if updated_at else "",
        freshness=worst_freshness(
            power_freshness,
            sen66_freshness,
            *(
                [ichijo_freshness]
                if active_ichijo is not None and ichijo_freshness is not None
                else []
            ),
        ),
    )


def freshness_for(measured_at: datetime | None, now: datetime) -> FreshnessStatus:
    if measured_at is None:
        return FreshnessStatus.UNAVAILABLE
    age_seconds = max(0.0, (_as_jst(now) - _as_jst(measured_at)).total_seconds())
    if age_seconds <= NORMAL_MAX_AGE_SECONDS:
        return FreshnessStatus.NORMAL
    if age_seconds <= DELAYED_MAX_AGE_SECONDS:
        return FreshnessStatus.DELAYED
    return FreshnessStatus.UNAVAILABLE


def worst_freshness(*statuses: FreshnessStatus) -> FreshnessStatus:
    ranking = {status: index for index, status in enumerate(FreshnessStatus)}
    return max(statuses, key=ranking.__getitem__)



def power_label(direction: PowerDirection) -> str:
    return {
        PowerDirection.PURCHASE: "現在の買電",
        PowerDirection.SALE: "現在の売電",
        PowerDirection.NEUTRAL: "現在の電力",
        PowerDirection.UNAVAILABLE: "現在の電力",
    }[direction]



def format_grid_flow(
    ichijo: LatestIchijoPowerFlow | None,
) -> tuple[str, str, PowerDirection]:
    if ichijo is None:
        return "", "--", PowerDirection.UNAVAILABLE

    if ichijo.grid_export_power_w > 0:
        return (
            "売電中",
            f"{ichijo.grid_export_power_w / 1000:.2f}",
            PowerDirection.SALE,
        )

    if ichijo.grid_import_power_w > 0:
        return (
            "買電中",
            f"{ichijo.grid_import_power_w / 1000:.2f}",
            PowerDirection.PURCHASE,
        )

    return "収支なし", "0.00", PowerDirection.NEUTRAL


def format_battery_power(
    ichijo: LatestIchijoPowerFlow | None,
) -> tuple[str, str]:
    if ichijo is None:
        return "", "--"

    if ichijo.battery_charge_power_w > 0:
        return "充電", f"{ichijo.battery_charge_power_w / 1000:.2f}"

    if ichijo.battery_discharge_power_w > 0:
        return "放電", f"{ichijo.battery_discharge_power_w / 1000:.2f}"

    return "充放電", "0.00"


def format_power(power: LatestPower | None) -> tuple[str, str, PowerDirection]:
    if power is None:
        return "--", "データなし", PowerDirection.UNAVAILABLE
    if power.net_power_w > 0:
        return f"{abs(power.net_power_w) / 1000:.2f}", "買電", PowerDirection.PURCHASE
    if power.net_power_w < 0:
        return f"{abs(power.net_power_w) / 1000:.2f}", "売電", PowerDirection.SALE
    return "0.00", "収支なし", PowerDirection.NEUTRAL



def format_optional(value: float | None, decimals: int) -> str:
    return "--" if value is None else f"{value:.{decimals}f}"


def oldest_available(*timestamps: datetime | None) -> datetime | None:
    available = [_as_jst(timestamp) for timestamp in timestamps if timestamp is not None]
    return min(available) if available else None


def _as_jst(timestamp: datetime) -> datetime:
    return timestamp.replace(tzinfo=JST) if timestamp.tzinfo is None else timestamp.astimezone(JST)
