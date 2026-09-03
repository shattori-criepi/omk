"""Display-only fixture fallback for exhibition mode."""
from dataclasses import replace
import json
from pathlib import Path
from app.display_items import DisplayItem

_FIXTURE = Path(__file__).parent / "demo" / "readings.json"

def apply_demo_fallback(items: list[DisplayItem]) -> list[DisplayItem]:
    """Replace only stale/unavailable readings, without touching any data store."""
    values = json.loads(_FIXTURE.read_text(encoding="utf-8"))["values"]
    return [replace(item, value=str(values[item.semantic_role]), freshness="normal", last_received_at="", short_label=f"{item.short_label or item.label}（模擬）")
            if item.freshness in {"stale", "unavailable"} and item.semantic_role in values else item for item in items]
