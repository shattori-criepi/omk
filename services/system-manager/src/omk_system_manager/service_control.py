"""Fixed, non-shell systemctl operations for the B-route service."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

BROUTE_SERVICE: Final = "omk-broute-meter.service"


class ServiceControlError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BRouteServiceController:
    """Run only the two sudoers-authorized commands with fixed arguments."""

    def __init__(self, systemctl_path: str) -> None:
        if not Path(systemctl_path).is_absolute():
            raise ValueError("OMK_SYSTEMCTL_PATH must be absolute")
        self._systemctl_path = systemctl_path

    def restart_and_verify(self) -> None:
        self._run("restart", "credentials_saved_restart_failed")
        self._run("is-active", "credentials_saved_service_inactive")

    def is_active(self) -> bool:
        result = self._execute("is-active")
        # systemctl's documented success signal is exit status 0.  Preserve
        # its textual state as a defensive success signal too: wrappers may
        # surface a completed command with the active state on stdout.
        return result.returncode == 0 or result.stdout.strip().casefold() == "active"

    def _run(self, verb: str, failure_code: str) -> None:
        result = self._execute(verb)
        if result.returncode != 0:
            raise ServiceControlError(failure_code)

    def _execute(self, verb: str) -> subprocess.CompletedProcess[str]:
        # No value from an HTTP request is ever included in this argv list.
        return subprocess.run(
            ["sudo", "-n", self._systemctl_path, verb, BROUTE_SERVICE],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
            shell=False,
        )
