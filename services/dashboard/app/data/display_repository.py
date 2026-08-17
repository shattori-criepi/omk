"""Read Phase 1 generic latest items without modifying collector-owned files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogItem:
    id: str
    topic: str
    device_id: str
    field: str
    value_type: str
    last_received_at: str


@dataclass(frozen=True)
class LatestDisplayItem(CatalogItem):
    value: Any
    measured_at: str | None
    received_at: str


class DisplayRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def catalog(self) -> list[CatalogItem]:
        path = self.root / "catalog.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload["items"]
            if payload.get("version") != 1 or not isinstance(items, list):
                raise ValueError("unsupported catalog")
            return [CatalogItem(**item) for item in items]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            LOGGER.warning("Unable to read display catalog: %s", error)
            return []

    def item(self, item_id: str) -> LatestDisplayItem | None:
        if not item_id.startswith("item_v1_") or len(item_id) != 72:
            return None
        path = self.root / "items" / f"{item_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or payload.get("id") != item_id:
                raise ValueError("invalid item")
            return LatestDisplayItem(
                id=payload["id"], topic=payload["topic"], device_id=payload["device_id"],
                field=payload["field"], value_type=payload["value_type"],
                value=payload["value"], measured_at=payload.get("measured_at"),
                received_at=payload["received_at"], last_received_at=payload["received_at"],
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            LOGGER.warning("Unable to read display item %s: %s", item_id, error)
            return None
