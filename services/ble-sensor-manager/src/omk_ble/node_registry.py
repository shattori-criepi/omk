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
                nodes = data.get("nodes") if isinstance(data, dict) else None
                if not isinstance(nodes, dict) or any(not isinstance(node, dict) for node in nodes.values()):
                    raise RuntimeError("Invalid Node registry; refusing to replace persisted registration")
                return nodes
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("Cannot read Node registry; registration is unchanged") from error

    def update(self, node_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            nodes = self.list()
            current = dict(nodes.get(node_id, {"node_id": node_id}))
            current.update(values)
            nodes[node_id] = current
            self._write(nodes)
            return current

    def clear_registration(self, node_id: str, **values: Any) -> dict[str, Any]:
        """Remove only Node registration metadata while retaining its identity and status."""
        with self._lock:
            nodes = self.list()
            current = dict(nodes.get(node_id, {"node_id": node_id}))
            for key in ("logical_id", "requested_logical_id", "request_state", "ack_seen_at"):
                current.pop(key, None)
            current.update(values)
            nodes[node_id] = current
            self._write(nodes)
            return current

    def _write(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"nodes": nodes}, ensure_ascii=False, indent=2) + "\n"
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as output:
                temporary_path = output.name
                os.fchmod(output.fileno(), 0o640)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            raise RuntimeError("Cannot persist Node registration; no config was sent") from error
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
