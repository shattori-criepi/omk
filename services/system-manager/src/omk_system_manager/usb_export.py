"""Fixed-command asynchronous USB export controller."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import logging
import subprocess
import threading
from zoneinfo import ZoneInfo

DATASETS = frozenset(("broute_power", "broute_cumulative_energy", "broute_interval_energy", "sen66", "ichijo_power_flow", "ble_environment", "ble_motion", "ble_contact", "ble_power"))
JST = ZoneInfo("Asia/Tokyo")
LOGGER = logging.getLogger(__name__)

@dataclass
class UsbExportJob:
    state: str = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    file_name: str | None = None
    error_code: str | None = None

    def public(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value is not None}

class UsbExportController:
    def __init__(self, command: str) -> None:
        self.command, self.job, self.lock = command, UsbExportJob(), threading.Lock()

    def status(self) -> dict:
        result = subprocess.run([self.command, "--status"], capture_output=True, text=True, check=False, shell=False, timeout=10)
        if result.returncode:
            return {"state": "error"}
        try:
            data = json.loads(result.stdout)
            return {key: data.get(key) for key in ("state", "device", "filesystem", "label", "size", "free_space", "mount_state", "identity")}
        except (ValueError, TypeError):
            return {"state": "error"}

    def start(self, from_date: str, to_date: str, datasets: list[str], expected_identity: str) -> bool:
        with self.lock:
            if self.job.state == "running": return False
            if self.status().get("identity") != expected_identity:
                raise ValueError("usb_changed")
            self.job = UsbExportJob("running", datetime.now(JST).isoformat())
            threading.Thread(target=self._run, args=(from_date, to_date, datasets, expected_identity), daemon=True).start()
            return True

    def _run(self, from_date: str, to_date: str, datasets: list[str], expected_identity: str) -> None:
        args = [self.command, "--from", from_date, "--to", to_date, "--expected-identity", expected_identity]
        for dataset in datasets: args += ["--dataset", dataset]
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False, shell=False, timeout=None)
            with self.lock:
                self.job.finished_at = datetime.now(JST).isoformat()
                if result.returncode == 0:
                    self.job.state = "succeeded"; self.job.file_name = Path(result.stdout.strip()).name
                    LOGGER.info("USB export completed")
                else:
                    self.job.state = "failed"; self.job.error_code = _safe_error_code(result.stderr)
                    LOGGER.warning("USB export failed: %s", self.job.error_code)
        except (OSError, subprocess.SubprocessError):
            with self.lock:
                self.job.state = "failed"; self.job.finished_at = datetime.now(JST).isoformat(); self.job.error_code = "export_failed"
                LOGGER.warning("USB export failed: export_failed")

def _safe_error_code(stderr: str) -> str:
    known = {"not_present", "ambiguous", "unsupported_filesystem", "identity_unavailable", "usb_changed", "busy", "mount_not_writable", "export_failed", "sync_failed", "unmount_failed"}
    return next((code for code in known if code in stderr), "export_failed")
