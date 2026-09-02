from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from omk_system_manager.main import UsbExportRequest
from omk_system_manager.usb_export import UsbExportController

TOKEN = "token"
def headers(): return {"Authorization": f"Bearer {TOKEN}"}

class FakeUsb:
    def __init__(self): self.job = type("Job", (), {"public": lambda s: {"state":"idle"}})(); self.started=[]
    def status(self): return {"state":"available","filesystem":"vfat","mount_state":"unmounted"}
    def start(self, f, t, d): self.started.append((f,t,d)); return True

@pytest.mark.parametrize("payload", [
    {"from":"2026-08-02","to":"2026-08-01","datasets":["sen66"]},
    {"from":"invalid","to":"2026-08-01","datasets":["sen66"]},
    {"from":"2026-08-01","to":"2026-08-02","datasets":[]},
    {"from":"2026-08-01","to":"2026-08-02","datasets":["unknown"]},
])
def test_usb_request_validation(payload):
    body=UsbExportRequest.model_validate(payload)
    with pytest.raises(ValueError): UsbExportRequest.validate_request(body)

def test_controller_status_uses_fixed_command_without_shell(monkeypatch):
    calls=[]
    def run(args, **kwargs):
        calls.append((args,kwargs)); return subprocess.CompletedProcess(args,0,'{"state":"available"}','')
    monkeypatch.setattr('omk_system_manager.usb_export.subprocess.run',run)
    assert UsbExportController('/usr/local/bin/omk-export-usb').status()['state']=='available'
    assert calls[0][0]==['/usr/local/bin/omk-export-usb','--status'] and calls[0][1]['shell'] is False
