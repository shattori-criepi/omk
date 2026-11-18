"""Finite schedules and boot counters: compile the production pure C, UBSan."""
import json
import os
import subprocess
from pathlib import Path
import pytest
BASE=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def ab_host(tmp_path_factory):
    exe=tmp_path_factory.mktemp('ble-scan-ab')/'ab'
    cjson=Path(os.environ['OMK_TEST_CJSON_DIR'])
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all','-I',str(BASE/'src'),'-I',str(cjson),str(BASE/'tests/ble_scan_ab_host.c'),*[str(BASE/'src'/n) for n in ('ble_scan_trial.c','ble_scan_metrics.c','ble_scan_command.c')],str(cjson/'cJSON.c'),'-lm','-o',str(exe)],check=True)
    return exe
@pytest.mark.parametrize('case',range(51))
def test_state_and_metrics(ab_host,case):
    subprocess.run([str(ab_host),str(case)],check=True,capture_output=True,text=True)
