"""Read the B-route meter's non-sensitive runtime status file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

CONNECTION_STATES: Final = frozenset(
    {
        "starting",
        "scanning",
        "authenticating",
        "connected",
        "scan_error",
        "authentication_error",
        "connection_error",
        "stopped",
    }
)


def connection_status(path: Path, *, service_active: bool) -> tuple[str, str | None]:
    """Return a safe state; never infer a connection from systemd activity."""

    if not service_active:
        return "stopped", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "starting", None
    if not isinstance(data, dict):
        return "starting", None
    state = data.get("state")
    updated_at = data.get("updated_at")
    if state not in CONNECTION_STATES:
        return "starting", None
    return state, updated_at if isinstance(updated_at, str) else None
