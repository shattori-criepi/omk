"""Dashboard-only meanings for scalar measurements.

The collector stores raw fields; this module adds labels, formatting, grouping,
and future semantic roles without coupling those concerns back to collection.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetricDefinition:
    label: str
    unit: str = ""
    precision: int | None = None
    category: str = "その他"
    semantic_role: str | None = None
    selectable: bool = True
    states: dict[object, str] | None = None
    expected_update_interval_seconds: int | None = None


WHOLE_HOME_POWER_ROLES = frozenset({
    "grid_power", "pv_power", "load_power", "grid_import", "grid_export",
    "battery_charge", "battery_discharge", "pcs_output",
})


DEFINITIONS: dict[str, MetricDefinition] = {
    "temperature_c": MetricDefinition("温度", "℃", 1, "室内環境", "temperature"),
    "temperature_celsius": MetricDefinition("温度", "℃", 1, "室内環境", "temperature"),
    "relative_humidity_percent": MetricDefinition("湿度", "%", 0, "室内環境", "humidity"),
    "co2_ppm": MetricDefinition("CO₂濃度", "ppm", 0, "室内環境", "co2"),
    "pm1_0_ug_m3": MetricDefinition("PM1.0", "µg/m³", 1, "室内環境"),
    "pm2_5_ug_m3": MetricDefinition("PM2.5", "µg/m³", 1, "室内環境", "pm25"),
    "pm4_0_ug_m3": MetricDefinition("PM4.0", "µg/m³", 1, "室内環境"),
    "pm10_0_ug_m3": MetricDefinition("PM10", "µg/m³", 1, "室内環境"),
    "voc_index": MetricDefinition("VOC Index", "", 0, "室内環境", "voc"),
    "nox_index": MetricDefinition("NOx Index", "", 0, "室内環境", "nox"),
    # Collector values remain watts.  The Dashboard definition owns the
    # presentation conversion for whole-home energy-system measurements.
    "net_power_w": MetricDefinition("系統電力", "kW", 2, "電力メーター（Bルート）", "grid_power"),
    "power_w": MetricDefinition("消費電力", "W", 1, "個別機器", "device_power"),
    "switch_state": MetricDefinition("状態", category="個別機器", states={0: "OFF", 1: "ON", False: "OFF", True: "ON"}),
    "motion_state": MetricDefinition("人感", category="状態", semantic_role="motion", states={0: "不在", 1: "検知", False: "不在", True: "検知"}),
    "contact_state": MetricDefinition("開閉", category="状態", semantic_role="contact", states={0: "閉", 1: "開", False: "閉", True: "開"}),
    "pv_power_w": MetricDefinition("PV発電", "kW", 2, "太陽光・蓄電池", "pv_power"),
    "load_power_w": MetricDefinition("住宅内消費電力", "kW", 2, "太陽光・蓄電池", "load_power"),
    "grid_import_power_w": MetricDefinition("買電電力", "kW", 2, "太陽光・蓄電池", "grid_import"),
    "grid_export_power_w": MetricDefinition("売電電力", "kW", 2, "太陽光・蓄電池", "grid_export"),
    "battery_soc_percent": MetricDefinition("蓄電池残量", "%", 0, "太陽光・蓄電池", "battery_soc"),
    "battery_charge_power_w": MetricDefinition("蓄電池充電", "kW", 2, "太陽光・蓄電池", "battery_charge"),
    "battery_discharge_power_w": MetricDefinition("蓄電池放電", "kW", 2, "太陽光・蓄電池", "battery_discharge"),
    "battery_operating_state": MetricDefinition("蓄電池状態", category="太陽光・蓄電池", semantic_role="battery_state"),
    # B-route EA/EB values are read after each 00/30 minute boundary.  They
    # need a separate freshness policy from the 10-second instantaneous power.
    "cumulative_energy_import_kwh": MetricDefinition("買電積算", "kWh", 1, "電力メーター（Bルート）", "grid_import_energy_cumulative", expected_update_interval_seconds=30 * 60),
    "cumulative_energy_export_kwh": MetricDefinition("売電積算", "kWh", 1, "電力メーター（Bルート）", "grid_export_energy_cumulative", expected_update_interval_seconds=30 * 60),
    "import_energy_kwh": MetricDefinition("買電量", "kWh", 1, "電力メーター（Bルート）", "grid_import_energy", expected_update_interval_seconds=30 * 60),
    "export_energy_kwh": MetricDefinition("売電量", "kWh", 1, "電力メーター（Bルート）", "grid_export_energy", expected_update_interval_seconds=30 * 60),
    "pcs_ac_output_power_w": MetricDefinition("PCS出力", "kW", 2, "太陽光・蓄電池", "pcs_output"),
    "status": MetricDefinition("状態", selectable=False),
}


def definition_for(field: str) -> MetricDefinition:
    """Return a safe fallback so new scalar fields remain usable."""
    return DEFINITIONS.get(field, MetricDefinition(field.replace("_", " "), category="その他"))


def format_value(value: Any, definition: MetricDefinition) -> str:
    if definition.states is not None and value in definition.states:
        return definition.states[value]
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if definition.semantic_role in WHOLE_HOME_POWER_ROLES:
            value /= 1000
        if definition.precision is not None:
            return f"{value:.{definition.precision}f}"
        return str(value)
    return str(value)
