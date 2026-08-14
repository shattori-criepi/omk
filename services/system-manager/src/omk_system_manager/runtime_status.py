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
        "retry_wait",
        "scan_error",
        "authentication_error",
        "connection_error",
        "stopped",
    }
)


def connection_status(
    path: Path,
    *,
    service_active: bool,
) -> tuple[str, str | None, float | None]:
    """Return a safe state; never infer a connection from systemd activity."""

    if not service_active:
        return "stopped", None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "status_unavailable", None, None
    if not isinstance(data, dict):
        return "status_unavailable", None, None
    state = data.get("state")
    updated_at = data.get("updated_at")
    if state not in CONNECTION_STATES:
        return "status_unavailable", None, None
    retry_after_seconds = data.get("retry_after_seconds")
    if state != "retry_wait" or not isinstance(retry_after_seconds, (int, float)) or retry_after_seconds <= 0:
        retry_after_seconds = None
    return state, updated_at if isinstance(updated_at, str) else None, retry_after_seconds
