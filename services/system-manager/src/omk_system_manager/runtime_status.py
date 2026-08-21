"""Read the B-route meter's non-sensitive runtime status file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final

CONNECTION_STATES: Final = frozenset(
    {
        "starting",
        "adapter_missing",
        "adapter_initializing",
        "adapter_unresponsive",
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
) -> tuple[str, str | None, float | None, int | None, str]:
    """Return a safe state; never infer a connection from systemd activity."""

    if not service_active:
        return "stopped", None, None, None, "service_inactive"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "status_unavailable", None, None, None, "status_unavailable"
    if not isinstance(data, dict):
        return "status_unavailable", None, None, None, "status_unavailable"
    state = data.get("state")
    updated_at = data.get("updated_at")
    if state not in CONNECTION_STATES:
        return "status_unavailable", None, None, None, "status_unavailable"
    retry_after_seconds = data.get("retry_after_seconds")
    if state not in {"retry_wait", "adapter_unresponsive"} or not isinstance(retry_after_seconds, (int, float)) or retry_after_seconds <= 0:
        retry_after_seconds = None
    connection_attempt = data.get("connection_attempt")
    if not isinstance(connection_attempt, int) or isinstance(connection_attempt, bool) or connection_attempt < 1:
        connection_attempt = None
    return (
        state,
        updated_at if isinstance(updated_at, str) else None,
        retry_after_seconds,
        connection_attempt,
        "runtime_status",
    )


def request_immediate_retry(path: Path) -> None:
    """Atomically create a non-sensitive, one-shot retry request marker."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".retry-request.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as output:
            descriptor = -1
            output.write("retry\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
