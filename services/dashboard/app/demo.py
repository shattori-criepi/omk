"""Display-only fixture fallback for exhibition mode."""
from dataclasses import replace
import json
from pathlib import Path
from app.display_items import DisplayItem

_FIXTURE = Path(__file__).parent / "demo" / "readings.json"

def apply_demo_fallback(items: list[DisplayItem]) -> list[DisplayItem]:
    """Replace only stale/unavailable readings, without touching any data store."""
    values = json.loads(_FIXTURE.read_text(encoding="utf-8"))["values"]
    return [
        replace(item, value=value, freshness="normal", last_received_at="", short_label=f"{item.short_label or item.label}（模擬）")
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
