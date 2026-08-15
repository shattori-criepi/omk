"""Single-writer, atomically-persisted OMK Node metadata."""
from __future__ import annotations

import json
import os
import tempfile
from threading import RLock
from pathlib import Path
from typing import Any


class NodeRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def list(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                nodes = data.get("nodes", {})
                return nodes if isinstance(nodes, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

    def update(self, node_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            nodes = self.list()
            current = dict(nodes.get(node_id, {"node_id": node_id}))
            current.update(values)
            nodes[node_id] = current
            self._write(nodes)
            return current

    def _write(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"nodes": nodes}, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
            temporary_path = output.name
        os.replace(temporary_path, self.path)
        os.chmod(self.path, 0o640)
