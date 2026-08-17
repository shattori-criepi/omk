"""Atomic persistence, migration, and validation for Dashboard display blocks."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SIZES = {"large": 3, "medium": 2, "small": 1}
STANDARD_CAPACITY = 6
LAYOUT_PATTERNS = frozenset({"hero", "strip", "compact"})
# A block's grid size and its internal layout both affect how many short
# readings remain legible on a 7-inch display.  Small blocks deliberately stay
# strict; medium blocks can still show a practical sensor summary.
ITEM_LIMITS = {
    "large": {"hero": 6, "strip": 6, "compact": 5},
    "medium": {"hero": 5, "strip": 5, "compact": 5},
    "small": {"hero": 3, "strip": 3, "compact": 3},
}


def item_limit(size: str, layout_pattern: str) -> int:
    try:
        return ITEM_LIMITS[size][layout_pattern]
    except KeyError as error:
        raise SettingsError("ブロックの表示形式が正しくありません") from error


def default_layout_pattern(size: str) -> str:
    """Choose a practical layout for settings created before patterns existed."""
    return {"large": "hero", "medium": "strip", "small": "compact"}.get(size, "compact")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class DisplaySelection:
    """Legacy version-1 selection, retained for migration callers/tests."""

    item_id: str
    size: str


@dataclass(frozen=True)
class DisplayBlock:
    block_id: str
    group: str
    title: str
    size: str
    primary_item_id: str
    item_ids: tuple[str, ...]
    layout_pattern: str = "compact"

    def as_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "group": self.group,
            "title": self.title,
            "size": self.size,
            "primary_item_id": self.primary_item_id,
            "item_ids": list(self.item_ids),
            "layout_pattern": self.layout_pattern,
        }


@dataclass(frozen=True)
class DashboardSettings:
    blocks: tuple[DisplayBlock, ...]

    def as_dict(self) -> dict:
        return {
            "version": 2,
            "default_preset": "standard",
            "presets": {"standard": {"blocks": [block.as_dict() for block in self.blocks]}},
        }


class SettingsRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, available_groups: dict[str, str], defaults: list[DisplayBlock], item_migrations: dict[str, str] | None = None) -> DashboardSettings:
        if not self.path.exists():
            settings = DashboardSettings(tuple(defaults))
            self.save(settings, available_groups)
            return settings
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            settings, migrated = self._parse(payload, available_groups, allow_missing=True, item_migrations=item_migrations)
            if migrated:
                self.save(settings, available_groups)
            os.chmod(self.path, 0o644)
            return settings
        except (OSError, json.JSONDecodeError, TypeError, KeyError, SettingsError):
            # Keep a malformed file for diagnosis, but do not turn a usable
            # Dashboard into an empty layout merely because migration failed.
            return DashboardSettings(tuple(defaults))

    def save_payload(self, payload: object, available_groups: dict[str, str], item_migrations: dict[str, str] | None = None) -> DashboardSettings:
        settings, migrated = self._parse(payload, available_groups, allow_missing=False, item_migrations=item_migrations)
        if migrated and isinstance(payload, dict) and payload.get("version") == 1:
            raise SettingsError("旧形式の設定は保存できません")
        self.save(settings, available_groups)
        return settings

    def save(self, settings: DashboardSettings, available_groups: dict[str, str]) -> None:
        self._validate(settings, available_groups, allow_missing=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as output:
                temporary_path = Path(output.name)
                json.dump(settings.as_dict(), output, ensure_ascii=False, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o644)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def _parse(self, payload: object, available_groups: dict[str, str], *, allow_missing: bool, item_migrations: dict[str, str] | None = None) -> tuple[DashboardSettings, bool]:
        if not isinstance(payload, dict) or payload.get("default_preset") != "standard":
            raise SettingsError("設定形式が正しくありません")
        if payload.get("version") == 1:
            return self._migrate_v1(payload, available_groups, item_migrations), True
        if payload.get("version") != 2:
            raise SettingsError("設定形式が正しくありません")
        try:
            raw_blocks = payload["presets"]["standard"]["blocks"]
        except (KeyError, TypeError) as error:
            raise SettingsError("標準プリセット設定がありません") from error
        if not isinstance(raw_blocks, list):
            raise SettingsError("表示ブロックは配列で指定してください")
        blocks: list[DisplayBlock] = []
        migrated = False
        for block in raw_blocks:
            if not isinstance(block, dict) or not isinstance(block.get("item_ids"), list):
                raise SettingsError("表示ブロックの形式が正しくありません")
            layout_pattern = block.get("layout_pattern", default_layout_pattern(block.get("size")))
            group = block.get("group")
            title = block.get("title")
            if group == "太陽光・蓄電池":
                group = "一条パワコン"
                if title == "太陽光・蓄電池":
                    title = "一条パワコン"
                migrated = True
            item_ids, primary_item_id, item_migrated = _migrate_item_ids(tuple(block["item_ids"]), block.get("primary_item_id"), item_migrations)
            migrated |= item_migrated
            if "layout_pattern" not in block:
                # Pre-pattern V2 settings may contain every discovered value
                # from one source.  Keep the primary and the first values that
                # fit the sensible default rather than making the dashboard
                # unusable after the migration.
                item_ids = _limited_item_ids(item_ids, primary_item_id, block.get("size"), layout_pattern)
                migrated = True
            blocks.append(DisplayBlock(
                block_id=block.get("block_id"), group=group, title=title,
                size=block.get("size"), primary_item_id=primary_item_id,
                item_ids=item_ids, layout_pattern=layout_pattern,
            ))
        settings = DashboardSettings(tuple(blocks))
        self._validate(settings, available_groups, allow_missing=allow_missing)
        return settings, migrated

    def _migrate_v1(self, payload: dict, available_groups: dict[str, str], item_migrations: dict[str, str] | None) -> DashboardSettings:
        try:
            raw_items = payload["presets"]["standard"]["items"]
        except (KeyError, TypeError) as error:
            raise SettingsError("標準プリセット設定がありません") from error
        if not isinstance(raw_items, list):
            raise SettingsError("表示項目は配列で指定してください")
        grouped: dict[str, list[DisplaySelection]] = {}
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("item_id"), str) or item.get("size") not in SIZES:
                raise SettingsError("表示項目の形式が正しくありません")
            item_id = (item_migrations or {}).get(item["item_id"], item["item_id"])
            grouped.setdefault(available_groups.get(item_id, "保存済み設定"), []).append(DisplaySelection(item_id, item["size"]))
        blocks = []
        for index, (group, selections) in enumerate(grouped.items(), start=1):
            size = max(selections, key=lambda selection: SIZES[selection.size]).size
            blocks.append(DisplayBlock(
                block_id=f"block_{index}", group=group, title=group, size=size,
                primary_item_id=selections[0].item_id,
                item_ids=_limited_item_ids(tuple(dict.fromkeys(selection.item_id for selection in selections)), selections[0].item_id, size, default_layout_pattern(size)),
                layout_pattern=default_layout_pattern(size),
            ))
        settings = DashboardSettings(tuple(blocks))
        self._validate(settings, available_groups, allow_missing=True)
        return settings

    @staticmethod
    def _validate(settings: DashboardSettings, available_groups: dict[str, str], *, allow_missing: bool) -> None:
        block_ids = [block.block_id for block in settings.blocks]
        if len(block_ids) != len(set(block_ids)) or any(not isinstance(block_id, str) or not block_id for block_id in block_ids):
            raise SettingsError("ブロックIDが正しくありません")
        item_ids: list[str] = []
        for block in settings.blocks:
            if not isinstance(block.group, str) or not block.group or not isinstance(block.title, str) or not block.title.strip():
                raise SettingsError("ブロック名が正しくありません")
            if block.size not in SIZES or block.layout_pattern not in LAYOUT_PATTERNS or not block.item_ids or block.primary_item_id not in block.item_ids:
                raise SettingsError("ブロックの表示項目が正しくありません")
            if len(block.item_ids) > item_limit(block.size, block.layout_pattern):
                raise SettingsError("この表示形式に設定できる項目数を超えています")
            if len(block.item_ids) != len(set(block.item_ids)) or any(not isinstance(item_id, str) for item_id in block.item_ids):
                raise SettingsError("同じ表示項目を重複して選択できません")
            if not allow_missing:
                if any(item_id not in available_groups for item_id in block.item_ids):
                    raise SettingsError("存在しない表示項目が含まれています")
                if any(available_groups[item_id] != block.group for item_id in block.item_ids):
                    raise SettingsError("同じsourceの項目だけをブロックにできます")
            item_ids.extend(block.item_ids)
        if len(item_ids) != len(set(item_ids)):
            raise SettingsError("同じ表示項目を複数ブロックに配置できません")
        if sum(SIZES[block.size] for block in settings.blocks) > STANDARD_CAPACITY:
            raise SettingsError("表示領域がいっぱいです")


def _limited_item_ids(item_ids: tuple[str, ...], primary_item_id: object, size: object, layout_pattern: str) -> tuple[str, ...]:
    if not isinstance(primary_item_id, str) or primary_item_id not in item_ids:
        return item_ids
    return tuple([primary_item_id, *(item_id for item_id in item_ids if item_id != primary_item_id)][:item_limit(str(size), layout_pattern)])


def _migrate_item_ids(item_ids: tuple[str, ...], primary_item_id: object, item_migrations: dict[str, str] | None) -> tuple[tuple[str, ...], object, bool]:
    migrations = item_migrations or {}
    migrated_ids = tuple(dict.fromkeys(migrations.get(item_id, item_id) for item_id in item_ids))
    migrated_primary = migrations.get(primary_item_id, primary_item_id) if isinstance(primary_item_id, str) else primary_item_id
    return migrated_ids, migrated_primary, migrated_ids != item_ids or migrated_primary != primary_item_id
