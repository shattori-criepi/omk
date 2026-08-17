"""Atomic persistence and validation for Dashboard-only display settings."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SIZES = {"large": 3, "medium": 2, "small": 1}
STANDARD_CAPACITY = 6


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class DisplaySelection:
    item_id: str
    size: str

    def as_dict(self) -> dict[str, str]:
        return {"item_id": self.item_id, "size": self.size}


@dataclass(frozen=True)
class DashboardSettings:
    items: tuple[DisplaySelection, ...]

    def as_dict(self) -> dict:
        return {"version": 1, "default_preset": "standard", "presets": {"standard": {"items": [item.as_dict() for item in self.items]}}}


class SettingsRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, available_ids: set[str], defaults: list[DisplaySelection]) -> DashboardSettings:
        if not self.path.exists():
            settings = DashboardSettings(tuple(defaults))
            self.save(settings, available_ids)
            return settings
        try:
            return self._parse(json.loads(self.path.read_text(encoding="utf-8")), available_ids, allow_missing=True)
        except (OSError, json.JSONDecodeError, TypeError, KeyError, SettingsError):
            # Preserve a malformed file for diagnosis; present an empty, safe
            # configuration rather than allowing the display request to fail.
            return DashboardSettings(())

    def save_payload(self, payload: object, available_ids: set[str]) -> DashboardSettings:
        settings = self._parse(payload, available_ids, allow_missing=False)
        self.save(settings, available_ids)
        return settings

    def save(self, settings: DashboardSettings, available_ids: set[str]) -> None:
        self._validate(settings, available_ids, allow_missing=True)
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
            os.chmod(self.path, 0o640)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def _parse(self, payload: object, available_ids: set[str], *, allow_missing: bool) -> DashboardSettings:
        if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("default_preset") != "standard":
            raise SettingsError("設定形式が正しくありません")
        try:
            raw_items = payload["presets"]["standard"]["items"]
        except (KeyError, TypeError) as error:
            raise SettingsError("標準プリセット設定がありません") from error
        if not isinstance(raw_items, list):
            raise SettingsError("表示項目は配列で指定してください")
        selections = tuple(DisplaySelection(item_id=item.get("item_id"), size=item.get("size")) for item in raw_items if isinstance(item, dict))
        if len(selections) != len(raw_items):
            raise SettingsError("表示項目の形式が正しくありません")
        settings = DashboardSettings(selections)
        self._validate(settings, available_ids, allow_missing=allow_missing)
        return settings

    @staticmethod
    def _validate(settings: DashboardSettings, available_ids: set[str], *, allow_missing: bool) -> None:
        ids = [item.item_id for item in settings.items]
        if len(ids) != len(set(ids)):
            raise SettingsError("同じ表示項目を重複して選択できません")
        if any(not isinstance(item.item_id, str) or item.size not in SIZES for item in settings.items):
            raise SettingsError("表示項目または表示サイズが正しくありません")
        if not allow_missing and any(item_id not in available_ids for item_id in ids):
            raise SettingsError("存在しない表示項目が含まれています")
        if sum(SIZES[item.size] for item in settings.items) > STANDARD_CAPACITY:
            raise SettingsError("表示領域がいっぱいです")
