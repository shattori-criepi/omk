from pathlib import Path
import subprocess
import pytest
BASE=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def quality_host(tmp_path_factory):
    exe=tmp_path_factory.mktemp('quality')/'quality'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all','-I',str(BASE/'src'),str(BASE/'tests/quality_host.c'),str(BASE/'src/native_recovery.c'),'-o',str(exe)],check=True)
    return exe
@pytest.mark.parametrize('case',['intermittent','resource','healthy','burst','recovered_burst','receipt_deadline','queue_receipt','reset_and_stale','saturation','bounded_budget'])
def test_quality_policy(quality_host,case):
    subprocess.run([str(quality_host),case],check=True)
