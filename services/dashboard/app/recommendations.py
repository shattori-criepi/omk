"""Stable, semantic-role based presets for the general-audience dashboard."""

from __future__ import annotations

from collections import defaultdict

from app.data.settings_repository import DisplayBlock, default_layout_pattern, item_limit
from app.display_items import DisplayItem

BROUTE_GROUP = "電力メーター（Bルート）"

# The values deliberately live here, rather than scattered through the UI or
# repository.  Only relative usefulness matters; manufacturer names never do.
ENVIRONMENT_ROLE_SCORE = {
    "temperature": 30, "humidity": 30, "co2": 80,
    "pm25": 12, "voc": 10, "nox": 10,
}
ENVIRONMENT_ROLE_ORDER = ("temperature", "humidity", "co2", "pm25", "voc", "nox")
OTHER_ROLE_SCORE = {"device_power": 15, "contact": 10, "motion": 8}


def _items_by_group(candidates: list[DisplayItem]) -> dict[str, list[DisplayItem]]:
    groups: dict[str, list[DisplayItem]] = defaultdict(list)
    for item in candidates:
        if item.selectable:
            groups[item.group].append(item)
    return groups


def environment_group_score(items: list[DisplayItem]) -> int:
    """Rank a group by useful roles, strongly favoring temp/humidity/CO₂."""
    roles = {item.semantic_role for item in items}
    score = sum(ENVIRONMENT_ROLE_SCORE.get(role, 0) for role in roles)
    if {"temperature", "humidity", "co2"} <= roles:
        score += 100
    return score


def _ordered_environment_items(items: list[DisplayItem]) -> list[DisplayItem]:
    order = {role: index for index, role in enumerate(ENVIRONMENT_ROLE_ORDER)}
    return sorted(items, key=lambda item: (order.get(item.semantic_role, 99), item.short_label, item.id))


def _block(group: str, items: list[DisplayItem], size: str, index: int) -> DisplayBlock:
    ordered = _ordered_environment_items(items)
    # For non-environment blocks retain a useful semantic item first.
    if not any(item.semantic_role in ENVIRONMENT_ROLE_SCORE for item in ordered):
        ordered = sorted(items, key=lambda item: (item.semantic_role not in {"grid_power", "device_power", "contact", "motion"}, item.short_label, item.id))
    pattern = default_layout_pattern(size)
    selected = ordered[:item_limit(size, pattern)]
    return DisplayBlock(
        block_id=f"recommended_{index}", group=group, title=group, size=size,
        primary_item_id=selected[0].id, item_ids=tuple(item.id for item in selected), layout_pattern=pattern,
    )


def recommended_blocks(candidates: list[DisplayItem]) -> list[DisplayBlock]:
    """Produce at most three persisted blocks without using transient readings.

    Ichijo power-flow data remains available to custom layouts, but is purposely
    excluded here because it is specialist information for a general dashboard.
    """
    groups = _items_by_group(candidates)
    broute = groups.pop(BROUTE_GROUP, [])
    groups.pop("一条パワコン", None)
    environments = [(environment_group_score(items), group, items) for group, items in groups.items()]
    environments = [entry for entry in environments if entry[0] > 0]
    environments.sort(key=lambda entry: (-entry[0], entry[1]))
    used_groups: set[str] = set()
    chosen: list[tuple[str, list[DisplayItem]]] = []
    if broute:
        chosen.append((BROUTE_GROUP, broute))
        used_groups.add(BROUTE_GROUP)
    if environments:
        _, group, items = environments[0]
        chosen.append((group, items))
        used_groups.add(group)
    # A third general-purpose group is only useful after the headline blocks.
    others: list[tuple[int, str, list[DisplayItem]]] = []
    for group, items in groups.items():
        if group in used_groups:
            continue
        score = max((OTHER_ROLE_SCORE.get(item.semantic_role, 0) for item in items), default=0)
        # A second environment source is a fallback, not a co-equal headline:
        # it avoids duplicate temperature panels when a more distinct reading
        # (for example a plug) is available, while still filling a third slot.
        if score == 0 and environment_group_score(items) > 0:
            score = 1
        if score:
            others.append((score, group, items))
    others.sort(key=lambda entry: (-entry[0], entry[1]))
    if others:
        chosen.append((others[0][1], others[0][2]))
    chosen = chosen[:3]
    sizes = ("large", "large") if len(chosen) <= 2 else ("large", "medium", "small")
    return [_block(group, items, sizes[index], index + 1) for index, (group, items) in enumerate(chosen)]
