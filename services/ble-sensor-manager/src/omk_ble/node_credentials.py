"""Read per-node provisioning credentials without exposing their contents."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


_NODE_ID = re.compile(r"^[0-9a-f]{12}$")
_SECRET = re.compile(r"^[0-9a-f]{64}$")


class NodeCredentialError(RuntimeError):
    """A node cannot be provisioned safely with the available credentials."""


class NodeCredentialStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def read_pop(self, node_id: str) -> str:
        if not _NODE_ID.fullmatch(node_id):
            raise NodeCredentialError("Node provisioning credential is unavailable")
        credential = self.directory / f"{node_id}.json"
        try:
            mode = credential.stat().st_mode & 0o777
            if mode != 0o600:
                raise NodeCredentialError("Node provisioning credential is unavailable")
            data = json.loads(credential.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise NodeCredentialError("Node provisioning credential is unavailable") from error
        secret = data.get("provisioning_secret") if isinstance(data, dict) else None
        if data.get("node_id") != node_id or not isinstance(secret, str) or not _SECRET.fullmatch(secret):
            raise NodeCredentialError("Node provisioning credential is unavailable")
        return secret
