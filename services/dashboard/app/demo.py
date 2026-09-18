"""Display-only fixture fallback for exhibition mode."""
from dataclasses import replace
import json
from pathlib import Path
from app.display_items import DisplayItem
from app.data.settings_repository import DisplayBlock
from app.metric_definitions import MetricDefinition, definition_for
from app.recommendations import BROUTE_GROUP, ENVIRONMENT_ROLE_SCORE, environment_group_score

_FIXTURE = Path(__file__).parent / "demo" / "readings.json"

# These are presentation fields, not collector records. Fixture values already
# use display units (kW rather than the collector's watts).
_ENVIRONMENT_FIELDS = (
    "temperature_celsius", "relative_humidity_percent", "co2_ppm",
    "pm2_5_ug_m3", "voc_index", "nox_index",
)
_POWER_FIELDS = (
    "load_power_w", "pv_power_w", "grid_import_power_w", "grid_export_power_w",
    "battery_soc_percent", "battery_power_bidirectional",
)
_FRESHNESS_PRIORITY = {"normal": 0, "delayed": 1, "stale": 2, "unavailable": 3}


def demo_custom_preset(items: list[DisplayItem]) -> tuple[list[DisplayItem], tuple[DisplayBlock, ...]]:
    """Build the exhibition preset in memory, independently of saved custom.

    Outdoor values always use fixed fixtures, without consulting registrations
    or measured readings. The medium card intentionally holds six demo values,
    without changing the limits for user-editable persisted presets.
    """
    power = [item for item in items if item.group == "パワコン"]

    def select(candidates: list[DisplayItem], field: str, group: str) -> DisplayItem:
        synthetic = _synthetic_item(field, group)
        matches = [item for item in candidates if item.selectable and item.semantic_role == synthetic.semantic_role]
        best = min(matches, key=lambda item: (_FRESHNESS_PRIORITY.get(item.freshness, 3), item.id)) if matches else None
        return replace(best, group=group) if best else synthetic

    pcs_items = [select(power, field, "パワコン") for field in _POWER_FIELDS]
    environment_items = [select(items, field, "室内環境") for field in _ENVIRONMENT_FIELDS]
    outdoor_items = []
    for field, role, label in (("temperature_c", "outdoor_temperature", "外気温"),
                               ("relative_humidity_percent", "outdoor_humidity", "外気相対湿度")):
        item = _synthetic_item(field, "外気")
        outdoor_items.append(replace(
            item, id=f"demo:{role}",
            semantic_role=role, short_label=label, label=f"外気 {label}",
        ))
    candidates = apply_demo_fallback(pcs_items + environment_items + outdoor_items)
    blocks = tuple(
        DisplayBlock(block_id, group, group, size, values[0].id,
                     tuple(item.id for item in values), layout_pattern=pattern)
        for block_id, group, size, pattern, values in (
            ("demo:custom:pcs", "パワコン", "large", "hero", pcs_items),
            ("demo:custom:environment", "室内環境", "medium", "strip", environment_items),
            ("demo:custom:outdoor", "外気", "small", "compact", outdoor_items),
        )
    )
    return candidates, blocks


def demo_candidates(items: list[DisplayItem]) -> list[DisplayItem]:
    """Build an automatic-preset overlay; never pass it to settings/storage.

    Complete the strongest environment group, borrowing the best real reading
    for each role from other groups when necessary. Only these presentation
    copies change group; their IDs, values and source labels remain intact.
    Custom exhibition layouts use demo_custom_preset instead.
    """
    priority = _FRESHNESS_PRIORITY
    ranked = sorted(items, key=lambda item: (priority.get(item.freshness, 3), item.id))
    environments: dict[str, list[DisplayItem]] = {}
    for item in ranked:
        if item.selectable and item.semantic_role in ENVIRONMENT_ROLE_SCORE:
            environments.setdefault(item.group, []).append(item)
    group = min(environments, key=lambda name: (-environment_group_score(environments[name]), name)) if environments else "室内環境（デモ）"
    selected: list[DisplayItem] = []
    for field in _ENVIRONMENT_FIELDS:
        role = definition_for(field).semantic_role
        matches = [item for values in environments.values() for item in values if item.semantic_role == role]
        best = min(matches, key=lambda item: (priority.get(item.freshness, 3), item.group != group, item.id)) if matches else None
        selected.append(replace(best, group=group) if best else _synthetic_item(field, group))
    selected_ids = {item.id for item in selected}
    overlay = [item for item in ranked if item.id not in selected_ids and not (
        item.group == group and item.selectable and item.semantic_role in ENVIRONMENT_ROLE_SCORE
    )] + selected
    for field, target_group in [("net_power_w", BROUTE_GROUP), *((field, "パワコン") for field in _POWER_FIELDS)]:
        synthetic = _synthetic_item(field, target_group)
        if not any(item.selectable and item.group == target_group and item.semantic_role == synthetic.semantic_role for item in overlay):
            overlay.append(synthetic)
    return apply_demo_fallback(overlay)


def _synthetic_item(field: str, group: str) -> DisplayItem:
    definition = definition_for(field) if field != "battery_power_bidirectional" else MetricDefinition(
        "蓄電池充放電", "kW", 2, "太陽光・蓄電池", "battery_power_bidirectional",
    )
    return DisplayItem(
        id=f"demo:{definition.semantic_role}", label=f"{group} {definition.label}",
        group=group, topic="", device_id="demo", field=field, value_type="number",
        unit=definition.unit, category=definition.category, semantic_role=definition.semantic_role,
        selectable=True, last_received_at="", short_label=definition.label, source_kind="demo",
    )


def apply_demo_fallback(items: list[DisplayItem]) -> list[DisplayItem]:
    """Replace only stale/unavailable readings, without touching any data store."""
    values = json.loads(_FIXTURE.read_text(encoding="utf-8"))["values"]
    return [
        replace(item, value=value, freshness="normal", last_received_at="", source_kind="demo")
        if item.freshness in {"stale", "unavailable"} and (value := _fixture_value(item, values)) is not None else item
        for item in items
    ]


def _fixture_value(item: DisplayItem, values: dict[str, object]) -> str | None:
    """Use SwitchBot's anonymous device class before generic environment keys."""
    keys = (
        (f"switchbot_{item.field}", item.semantic_role, item.field)
        if item.device_id.startswith("th-") else (item.semantic_role, item.field)
    )
    return next((str(values[key]) for key in keys if isinstance(key, str) and key in values), None)
