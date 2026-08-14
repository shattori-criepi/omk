"""Non-sensitive, machine-readable B-route connection status."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

CONNECTION_STATES: Final = frozenset(
    {
        "starting",
        "adapter_missing",
        "adapter_initializing",
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


class RuntimeStatusStore:
    """Atomically persist status without accepting any credential data."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(
        self,
        state: str,
        *,
        now: datetime,
        retry_after_seconds: float | None = None,
        connection_attempt: int | None = None,
    ) -> None:
        if state not in CONNECTION_STATES:
            raise ValueError("Unsupported B-route connection state")
        if retry_after_seconds is not None and (
            state != "retry_wait" or retry_after_seconds <= 0
        ):
            raise ValueError("retry_after_seconds is only valid for retry_wait")
        if connection_attempt is not None and connection_attempt < 1:
            raise ValueError("connection_attempt must be positive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".status.", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                descriptor = -1
                payload = {"state": state, "updated_at": now.astimezone(UTC).isoformat()}
                if retry_after_seconds is not None:
                    payload["retry_after_seconds"] = retry_after_seconds
                if connection_attempt is not None:
                    payload["connection_attempt"] = connection_attempt
                json.dump(
                    payload,
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.path)
            _fsync_directory(self.path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class RetryRequestStore:
    """Consume a non-sensitive, one-shot request to end a retry wait early."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def consume(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
