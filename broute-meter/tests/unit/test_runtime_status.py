from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from broute_meter.runtime_status import RuntimeStatusStore


def test_runtime_status_is_atomic_non_sensitive_json(tmp_path) -> None:
    path = tmp_path / "status.json"
    RuntimeStatusStore(path).write("authenticating", now=datetime(2026, 8, 14, tzinfo=UTC))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"state": "authenticating", "updated_at": "2026-08-14T00:00:00+00:00"}
    assert "id" not in data
    assert "password" not in data


def test_runtime_status_rejects_unknown_states(tmp_path) -> None:
    with pytest.raises(ValueError):
        RuntimeStatusStore(tmp_path / "status.json").write("unsafe", now=datetime.now(UTC))


@pytest.mark.parametrize("state", ["adapter_missing", "adapter_initializing"])
def test_runtime_status_records_adapter_states(tmp_path, state: str) -> None:
    path = tmp_path / "status.json"

    RuntimeStatusStore(path).write(state, now=datetime.now(UTC))

    assert json.loads(path.read_text(encoding="utf-8"))["state"] == state


def test_runtime_status_records_non_sensitive_retry_wait(tmp_path) -> None:
    path = tmp_path / "status.json"
    RuntimeStatusStore(path).write(
        "retry_wait",
        now=datetime(2026, 8, 14, tzinfo=UTC),
        retry_after_seconds=30,
        connection_attempt=3,
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "retry_after_seconds": 30,
        "connection_attempt": 3,
        "state": "retry_wait",
        "updated_at": "2026-08-14T00:00:00+00:00",
    }
