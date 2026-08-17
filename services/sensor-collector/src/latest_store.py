"""Generic, atomically-written latest scalar values and their source catalog.

This module deliberately knows nothing about Dashboard labels, units, or layouts.
It records the scalar values that were actually received so a later consumer can
apply its own presentation rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

CATALOG_VERSION = 1
ITEM_ID_PREFIX = "item_v1_"
METADATA_FIELDS = frozenset({
    "device_id", "measured_at", "metered_at", "received_at", "quality",
    "quality_status", "errors", "error", "source", "relay_node_id",
    "uptime_ms", "firmware_name", "firmware_version", "id", "uuid", "uid",
    "timestamp", "time", "date",
})


def stable_item_id(topic: str, device_id: str, field: str) -> str:
    """Return a filesystem-safe, deterministic identifier for one source value."""
    encoded = f"{topic}\0{device_id}\0{field}".encode("utf-8")
    return ITEM_ID_PREFIX + hashlib.sha256(encoded).hexdigest()


def value_type(value: object) -> str | None:
    """Classify displayable scalar data without attaching presentation meaning."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number" if math.isfinite(float(value)) else None
    if isinstance(value, str):
        # Keep control characters and very large diagnostic strings out of the
        # catalog while allowing concise state enums such as "charging".
        if 0 < len(value) <= 64 and value.isprintable():
            return "string"
    return None


def is_display_candidate_field(field: str, value: object) -> bool:
    """Exclude broadly-recognisable transport and diagnostic metadata fields."""
    normalized = field.lower()
    if normalized in METADATA_FIELDS:
        return False
    if normalized.endswith(("_at", "_id", "_uuid", "_uid", "_raw", "_timestamp")):
        return False
    if normalized.startswith(("meta_", "internal_")) or "error" in normalized:
        return False
    return value_type(value) is not None


class GenericLatestStore:
    """Maintain ``items/<stable-id>.json`` and a durable, compact catalog."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.items_root = root / "items"
        self.catalog_path = root / "catalog.json"

    def write(self, record: dict[str, Any]) -> bool:
        source = _source_for(record)
        if source is None:
            return False
        topic, device_id, payload = source
        received_at = record.get("received_at")
        if not isinstance(received_at, str):
            return False
        measured_at = payload.get("measured_at")
        if not isinstance(measured_at, str):
            measured_at = None

        candidates = [
            (field, value, value_type(value))
            for field, value in payload.items()
            if isinstance(field, str) and is_display_candidate_field(field, value)
        ]
        if not candidates:
            return False

        try:
            catalog = self._load_catalog()
            catalog_items = {item["id"]: item for item in catalog["items"]}
            for field, value, scalar_type in candidates:
                assert scalar_type is not None
                item_id = stable_item_id(topic, device_id, field)
                item = {
                    "version": CATALOG_VERSION,
                    "id": item_id,
                    "topic": topic,
                    "device_id": device_id,
                    "field": field,
                    "value_type": scalar_type,
                    "value": value,
                    "measured_at": measured_at,
                    "received_at": received_at,
                    "source": {"qos": record.get("qos"), "retain": record.get("retain")},
                }
                self._atomic_json_write(self.items_root / f"{item_id}.json", item)
                catalog_items[item_id] = {
                    "id": item_id,
                    "topic": topic,
                    "device_id": device_id,
                    "field": field,
                    "value_type": scalar_type,
                    "last_received_at": received_at,
                }
            self._atomic_json_write(self.catalog_path, {
                "version": CATALOG_VERSION,
                "items": sorted(catalog_items.values(), key=lambda item: item["id"]),
            })
            return True
        except (OSError, TypeError, ValueError) as error:
            LOGGER.error("Failed to update generic latest store: %s", error)
            return False

    def _load_catalog(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {"version": CATALOG_VERSION, "items": []}
        try:
            catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read generic latest catalog: {error}") from error
        if not isinstance(catalog, dict) or catalog.get("version") != CATALOG_VERSION:
            raise ValueError("unsupported generic latest catalog")
        items = catalog.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in items):
            raise ValueError("invalid generic latest catalog items")
        return {"version": CATALOG_VERSION, "items": items}

    @staticmethod
    def _atomic_json_write(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            os.chmod(path, 0o644)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise


def _source_for(record: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    topic = record.get("topic")
    payload = record.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        return None
    parts = topic.split("/")
    device_id = payload.get("device_id")
    if len(parts) != 3 or parts[0] != "omk" or not parts[1] or not parts[2]:
        return None
    if not isinstance(device_id, str) or not device_id:
        return None
    return topic, device_id, payload
