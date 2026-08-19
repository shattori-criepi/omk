"""Read the OMK AP credentials from NetworkManager without persisting them."""

from __future__ import annotations

import os
import subprocess


class AccessPointCredentialError(RuntimeError):
    """Raised without including NetworkManager output or credentials."""


def _value(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise AccessPointCredentialError("OMKアクセスポイント設定を取得できません") from error
    return result.stdout.splitlines()[0] if result.stdout.splitlines() else ""


def read_access_point_status(profile: str) -> dict[str, str | bool]:
    ssid = _value(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile])
    if not ssid:
        raise AccessPointCredentialError("OMKアクセスポイント設定を取得できません")
    key_management = _value([
        "nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", profile,
    ])
    return {"ssid": ssid, "password_configured": key_management == "wpa-psk"}


def read_access_point_credentials(profile: str) -> dict[str, str]:
    status = read_access_point_status(profile)
    command = [] if os.geteuid() == 0 else ["sudo", "--"]
    command.extend([
        "nmcli", "--show-secrets", "-g", "802-11-wireless-security.psk",
        "connection", "show", profile,
    ])
    password = _value(command)
    if not password:
        raise AccessPointCredentialError("OMKアクセスポイントのパスワードを取得できません")
    return {"ssid": str(status["ssid"]), "password": password}
