"""Read non-secret OMK AP configuration needed for provisioning."""
from __future__ import annotations

import subprocess


class AccessPointConfigError(RuntimeError):
    pass


def read_ap_ssid(connection_name: str = "omk-ap") -> str:
    """Return only the public SSID; the PSK comes from a systemd credential."""
    try:
        result = subprocess.run(
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", connection_name],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AccessPointConfigError("OMK AP configuration is unavailable") from error
    ssid = result.stdout.strip()
    if result.returncode != 0 or not ssid:
        raise AccessPointConfigError("OMK AP configuration is unavailable")
    return ssid
