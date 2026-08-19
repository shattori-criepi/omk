"""Build Dashboard Display Items from collector-owned generic latest records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.data.display_repository import CatalogItem, DisplayRepository, LatestDisplayItem
from app.data.settings_repository import DisplayBlock, DisplaySelection
from app.metric_definitions import MetricDefinition, definition_for, format_value
from app.view_models import FreshnessStatus, format_grid_flow_values, freshness_for


@dataclass(frozen=True)
class DisplayItem:
    id: str
    label: str
    group: str
    topic: str
    device_id: str
    field: str
    value_type: str
    unit: str
    category: str
    semantic_role: str | None
    selectable: bool
    last_received_at: str
    value: str = "--"
    freshness: str = FreshnessStatus.UNAVAILABLE.value
    size: str = "small"
    short_label: str = ""
    source_item_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, str | bool | None]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class DisplayBlockView:
    id: str
    title: str
    group: str
    size: str
    layout_pattern: str
    primary: DisplayItem
    secondary: tuple[DisplayItem, ...]
    freshness: str
    last_received_at: str
    auxiliary_label: str = ""
    auxiliary_value: str = ""
    auxiliary_unit: str = ""
    auxiliary_flow: str = ""
    auxiliary_supported: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "size": self.size,
            "layout_pattern": self.layout_pattern,
            "primary": self.primary.as_dict(),
            "secondary": [item.as_dict() for item in self.secondary],
            "freshness": self.freshness,
            "last_received_at": self.last_received_at,
            "auxiliary_label": self.auxiliary_label,
            "auxiliary_value": self.auxiliary_value,
            "auxiliary_unit": self.auxiliary_unit,
            "auxiliary_flow": self.auxiliary_flow,
            "auxiliary_supported": self.auxiliary_supported,
        }


def source_group(item: CatalogItem) -> str:
    data_type = item.topic.rsplit("/", 1)[-1]
    if data_type == "power-flow":
        return "一条パワコン"
    if definition_for(item.field).category == "電力メーター（Bルート）":
        return "電力メーター（Bルート）"
    return item.device_id


def source_prefix(item: CatalogItem) -> str:
    data_type = item.topic.rsplit("/", 1)[-1]
    if data_type == "power-flow":
        return "一条パワコン"
    if item.field == "net_power_w":
        return "Bルート"
    return item.device_id


def candidate_for(
    item: CatalogItem,
    latest: LatestDisplayItem | None = None,
    now: datetime | None = None,
) -> DisplayItem:
    definition = definition_for(item.field)
    return _display_item(item, definition, latest, now)


def catalog_items_with_latest(repository: DisplayRepository, now: datetime, derived: list[DisplayItem] | None = None) -> list[DisplayItem]:
    """Build candidates with their actual latest reading for the admin UI."""
    return display_candidates(repository, now) + (derived or [])


def display_candidates(repository: DisplayRepository, now: datetime | None = None) -> list[DisplayItem]:
    """Return selectable Dashboard candidates, including virtual battery power."""
    raw = [candidate_for(item, repository.item(item.id), now) for item in repository.catalog()]
    paired: dict[tuple[str, str], dict[str, DisplayItem]] = {}
    for item in raw:
        if item.semantic_role in {"battery_charge", "battery_discharge"}:
            paired.setdefault((item.group, item.device_id), {})[item.semantic_role] = item
    hidden_ids: set[str] = set()
    virtual: list[DisplayItem] = []
    for values in paired.values():
        charge, discharge = values.get("battery_charge"), values.get("battery_discharge")
        if charge is not None and discharge is not None:
            hidden_ids.update((charge.id, discharge.id))
            virtual.append(_bidirectional_battery_item(charge, discharge, selectable=True))
    return [item for item in raw if item.id not in hidden_ids] + virtual


def display_item_migrations(candidates: list[DisplayItem]) -> dict[str, str]:
    """Map legacy charge/discharge selections to their virtual Dashboard item."""
    return {
        source_id: item.id
        for item in candidates
        if item.semantic_role == "battery_power_bidirectional"
        for source_id in item.source_item_ids
    }


def selected_items(
    repository: DisplayRepository,
    selections: tuple[DisplaySelection, ...],
    now: datetime,
) -> list[DisplayItem]:
    catalog = {item.id: item for item in repository.catalog()}
    displayed: list[DisplayItem] = []
    for selection in selections:
        catalog_item = catalog.get(selection.item_id)
        if catalog_item is None:
            # A catalog normally retains items forever. This preserves the slot
            # if a file was manually removed, instead of silently reflowing it.
            displayed.append(DisplayItem(selection.item_id, "利用できない表示項目", "保存済み設定", "", "", "", "unknown", "", "その他", None, False, "", size=selection.size))
            continue
        latest = repository.item(selection.item_id)
        displayed.append(_display_item(catalog_item, definition_for(catalog_item.field), latest, now, selection.size))
    return displayed


def selected_blocks(
    repository: DisplayRepository,
    blocks: tuple[DisplayBlock, ...],
    now: datetime,
    candidates: list[DisplayItem] | None = None,
) -> list[DisplayBlockView]:
    """Resolve persisted block membership into one display-ready snapshot."""
    catalog = {item.id: item for item in (candidates if candidates is not None else display_candidates(repository, now))}
    rendered: list[DisplayBlockView] = []
    for block in blocks:
        values: list[DisplayItem] = []
        for item_id in block.item_ids:
            item = catalog.get(item_id)
            if item is None:
                values.append(DisplayItem(item_id, "利用できない表示項目", block.group, "", "", "", "unknown", "", "その他", None, False, ""))
            else:
                values.append(item)
        primary_id = block.primary_item_id
        primary = next((item for item in values if item.id == primary_id), values[0])
        secondary = tuple(item for item in values if item.id != primary.id)
        auxiliary_supported = (
            block.group == "一条パワコン"
            and block.layout_pattern == "hero"
            and primary.semantic_role == "load_power"
        )
        auxiliary_label, auxiliary_value, auxiliary_flow = _grid_flow_auxiliary(repository, catalog.values()) if auxiliary_supported else ("", "", "")
        statuses = [item.freshness for item in values]
        freshness = FreshnessStatus.UNAVAILABLE.value
        if FreshnessStatus.UNAVAILABLE.value not in statuses:
            freshness = FreshnessStatus.DELAYED.value if FreshnessStatus.DELAYED.value in statuses else FreshnessStatus.NORMAL.value
        elif FreshnessStatus.NORMAL.value in statuses or FreshnessStatus.DELAYED.value in statuses:
            freshness = FreshnessStatus.DELAYED.value
        last_received_at = max((item.last_received_at for item in values if item.last_received_at), default="")
        rendered.append(DisplayBlockView(
            id=block.block_id, title=_display_block_title(block.title, block.group), group=block.group, size=block.size,
            layout_pattern=block.layout_pattern,
            primary=primary, secondary=secondary, freshness=freshness, last_received_at=last_received_at,
            auxiliary_label=auxiliary_label, auxiliary_value=auxiliary_value,
            auxiliary_unit="kW" if auxiliary_label else "", auxiliary_flow=auxiliary_flow,
            auxiliary_supported=auxiliary_supported,
        ))
    return rendered


def _display_block_title(title: str, group: str) -> str:
    """Keep the energy-system block compact without overriding custom titles."""
    return "一条パワコン" if group == "一条パワコン" and title in {group, "太陽光・蓄電池"} else title


def _grid_flow_auxiliary(repository: DisplayRepository, items) -> tuple[str, str, str]:
    """Reuse the legacy grid-flow order and zero handling for hero blocks."""
    by_role = {item.semantic_role: item for item in items if item.group == "一条パワコン"}
    import_item, export_item = by_role.get("grid_import"), by_role.get("grid_export")
    import_latest = repository.item(import_item.id) if import_item and import_item.freshness != FreshnessStatus.UNAVAILABLE.value else None
    export_latest = repository.item(export_item.id) if export_item and export_item.freshness != FreshnessStatus.UNAVAILABLE.value else None
    label, value, flow = format_grid_flow_values(
        _numeric_value(import_latest.value) if import_latest is not None else None,
        _numeric_value(export_latest.value) if export_latest is not None else None,
    )
    if label in {"買電中", "売電中"}:
        return label, value, flow.value
    return "", "", ""


def _display_item(
    item: CatalogItem,
    definition: MetricDefinition,
    latest: LatestDisplayItem | None,
    now: datetime | None = None,
    size: str = "small",
) -> DisplayItem:
    label = f"{source_prefix(item)} {definition.label}"
    freshness = FreshnessStatus.UNAVAILABLE
    value = "--"
    if latest is not None and now is not None:
        received_at = _parse_time(latest.received_at)
        freshness = freshness_for(received_at, now) if received_at else FreshnessStatus.UNAVAILABLE
        if freshness != FreshnessStatus.UNAVAILABLE:
            value = format_value(latest.value, definition)
    return DisplayItem(
        id=item.id, label=label, group=source_group(item), topic=item.topic,
        device_id=item.device_id, field=item.field, value_type=item.value_type,
        unit=definition.unit, category=definition.category,
        semantic_role=definition.semantic_role, selectable=definition.selectable,
        last_received_at=item.last_received_at, value=value, freshness=freshness.value,
        size=size, short_label=definition.label,
        source_item_ids=(item.id,),
    )


def _bidirectional_battery_item(charge: DisplayItem, discharge: DisplayItem, *, selectable: bool) -> DisplayItem:
    """Build the one Dashboard-only representation of charge/discharge."""
    charge_value = _numeric_value(charge.value)
    discharge_value = _numeric_value(discharge.value)
    if discharge_value is not None and discharge_value > 0.005:
        direction, value = "放電", discharge_value
    elif charge_value is not None and charge_value > 0.005:
        direction, value = "充電", charge_value
    elif charge_value is not None or discharge_value is not None:
        direction, value = "待機", 0.0
    else:
        direction, value = "蓄電池", None

    representative = discharge if discharge_value is not None else charge
    freshness = _combined_freshness(charge.freshness, discharge.freshness)
    virtual_id = f"virtual:battery_power_bidirectional:{representative.device_id}"
    return DisplayItem(
        id=virtual_id, label=f"{representative.group} 蓄電池充放電", short_label=f"蓄電池 {direction}",
        group=representative.group, topic=representative.topic, device_id=representative.device_id,
        field="battery_power_bidirectional", value_type="number", unit="kW",
        category=representative.category, semantic_role="battery_power_bidirectional",
        selectable=selectable, last_received_at=max(charge.last_received_at, discharge.last_received_at),
        value=f"{value:.2f}" if value is not None and freshness != FreshnessStatus.UNAVAILABLE.value else "--",
        freshness=freshness,
        source_item_ids=(charge.id, discharge.id),
    )


def _numeric_value(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _combined_freshness(*statuses: str) -> str:
    if FreshnessStatus.NORMAL.value in statuses:
        return FreshnessStatus.DELAYED.value if FreshnessStatus.DELAYED.value in statuses else FreshnessStatus.NORMAL.value
    if FreshnessStatus.DELAYED.value in statuses:
        return FreshnessStatus.DELAYED.value
    return FreshnessStatus.UNAVAILABLE.value


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None
