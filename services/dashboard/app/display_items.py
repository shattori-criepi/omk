"""Build Dashboard Display Items from collector-owned generic latest records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.data.display_repository import CatalogItem, DisplayRepository, LatestDisplayItem
from app.data.settings_repository import DisplaySelection
from app.metric_definitions import MetricDefinition, definition_for, format_value
from app.view_models import FreshnessStatus, freshness_for


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

    def as_dict(self) -> dict[str, str | bool | None]:
        return self.__dict__.copy()


def source_group(item: CatalogItem) -> str:
    data_type = item.topic.rsplit("/", 1)[-1]
    if data_type == "power-flow":
        return "太陽光・蓄電池"
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


def catalog_items_with_latest(repository: DisplayRepository, now: datetime) -> list[DisplayItem]:
    """Build candidates with their actual latest reading for the admin UI."""
    return [candidate_for(item, repository.item(item.id), now) for item in repository.catalog()]


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
        size=size,
    )


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None
