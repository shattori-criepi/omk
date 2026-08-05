"""Small durable SQLite outbox for Harvest records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class RetryQueue:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS harvest_outbox (
            id INTEGER PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL,
            available_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)""")
        self._connection.commit()

    def enqueue(self, payload: dict[str, Any], now: datetime) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        value = now.isoformat()
        self._connection.execute("INSERT INTO harvest_outbox(payload,created_at,available_at) VALUES(?,?,?)", (encoded, value, value))
        self._connection.commit()

    def next_due(self, now: datetime) -> tuple[int, dict[str, Any]] | None:
        row = self._connection.execute("SELECT id,payload FROM harvest_outbox WHERE available_at <= ? ORDER BY id LIMIT 1", (now.isoformat(),)).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def mark_sent(self, item_id: int) -> None:
        self._connection.execute("DELETE FROM harvest_outbox WHERE id=?", (item_id,))
        self._connection.commit()

    def postpone(self, item_id: int, now: datetime) -> None:
        attempts = self._connection.execute("SELECT attempts FROM harvest_outbox WHERE id=?", (item_id,)).fetchone()[0] + 1
        delay = min(60, 2 ** min(attempts, 6))
        self._connection.execute("UPDATE harvest_outbox SET attempts=?,available_at=? WHERE id=?", (attempts, (now + timedelta(seconds=delay)).isoformat(), item_id))
        self._connection.commit()

    def discard_expired(self, now: datetime, max_age_seconds: int) -> int:
        cutoff = (now - timedelta(seconds=max_age_seconds)).isoformat()
        cursor = self._connection.execute("DELETE FROM harvest_outbox WHERE created_at < ?", (cutoff,))
        self._connection.commit()
        return cursor.rowcount

    def count(self) -> int:
        return int(self._connection.execute("SELECT count(*) FROM harvest_outbox").fetchone()[0])
