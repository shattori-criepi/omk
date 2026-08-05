"""Harvest HTTP adapter, deliberately independent from MQTT and SQLite."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


class HarvestClient:
    def __init__(self, endpoint: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def send(self, payload: dict[str, Any]) -> None:
        request = Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Harvest returned HTTP {response.status}")
