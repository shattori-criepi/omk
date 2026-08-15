"""Read AP credentials injected by systemd without exposing the PSK."""
from __future__ import annotations

import os
from pathlib import Path


class ApCredentialError(RuntimeError):
    pass


def read_ap_psk() -> str:
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise ApCredentialError("Wi-Fi provisioning credential is unavailable")
    try:
        value = (Path(directory) / "omk_ap_psk").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ApCredentialError("Wi-Fi provisioning credential is unavailable") from error
    if not value:
        raise ApCredentialError("Wi-Fi provisioning credential is unavailable")
    return value
