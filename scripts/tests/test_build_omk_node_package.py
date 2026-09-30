"""Run the production package script with fake Git/PIO and synthetic artifacts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def build(tmp_path):
    repository = tmp_path/'repo with spaces'; repository.mkdir()
    project = repository/'firmware/esp32/omk-node/.pio/build/atom-s3-lite'
    project.mkdir(parents=True)
    for name in ('bootloader.bin', 'partitions.bin', 'firmware.bin', 'secret.bin'):
        (project/name).write_bytes(name.encode())
    commands = tmp_path/'bin'; commands.mkdir()
    git = commands/'git'
    git.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
args = sys.argv[1:]
if '--show-toplevel' in args: print(os.environ['FIXTURE_REPO'])
elif 'HEAD' in args: print('a'*40)
elif 'status' in args:
    assert '--porcelain' in args and '--untracked-files=all' in args
    built = Path(os.environ['BUILD_EVENT']).exists()
    if os.environ.get('STATUS_FAIL') or (built and os.environ.get('STATUS_FAIL_AFTER_BUILD')): sys.exit(128)
    if os.environ.get('DIRTY') or (built and os.environ.get('DIRTY_AFTER_BUILD')): print('?? source.c')
else: sys.exit(3)
'''); git.chmod(0o755)
    pio = commands/'pio'
    pio.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path

base = ['run', '-d', os.environ['FIXTURE_REPO']+'/firmware/esp32/omk-node', '-e', 'atom-s3-lite']
args = sys.argv[1:]
clean_event = Path(os.environ['CLEAN_EVENT'])

if args == base + ['-t', 'clean']:
    clean_event.write_text('cleaned')
    sys.exit(0)

assert args == base
assert clean_event.exists()
Path(os.environ['BUILD_EVENT']).write_text('built')
if os.environ.get('BUILD_FAIL'): sys.exit(1)
'''); pio.chmod(0o755)
    event = tmp_path/'build-event'
    clean_event = tmp_path/'clean-event'
    env = {
        **os.environ,
        'PATH': str(commands)+':'+os.environ['PATH'],
        'FIXTURE_REPO': str(repository),
        'BUILD_EVENT': str(event),
        'CLEAN_EVENT': str(clean_event),
    }
    def run(**extra):
        return subprocess.run(['bash', str(ROOT/'scripts/build-omk-node-package.sh')], cwd=repository, env={**env, **extra}, capture_output=True, text=True)
    return run, repository, event


def test_package_has_only_fixed_artifacts_and_source_sha(build):
    run, root, event = build
    assert run().returncode == 0 and event.exists()
    destination = root/'firmware/esp32/omk-node/prebuilt/atom-s3-lite'
    assert {p.name for p in destination.iterdir()} == {'manifest.json', 'bootloader.bin', 'partitions.bin', 'firmware.bin'}
    manifest = json.loads((destination/'manifest.json').read_text())
    assert manifest['source_commit'] == 'a'*40 and manifest['schema_version'] == 1
    assert manifest['target'] == 'atom-s3-lite' and manifest['chip'] == 'esp32s3'
    assert [s['offset'] for s in manifest['segments']] == ['0x00000000', '0x00008000', '0x00010000']
    for segment in manifest['segments']:
        data = (destination/segment['filename']).read_bytes()
        assert len(data) == segment['size'] and hashlib.sha256(data).hexdigest() == segment['sha256']


@pytest.mark.parametrize('condition', ['DIRTY', 'DIRTY_AFTER_BUILD', 'BUILD_FAIL'])
def test_dirty_or_failed_build_cannot_publish(build, condition):
    run, root, event = build
    assert run(**{condition:'1'}).returncode != 0
    assert not (root/'firmware/esp32/omk-node/prebuilt/atom-s3-lite').exists()
    assert event.exists() == (condition != 'DIRTY')


def test_fresh_setup_serial_permission_and_pinned_esptool():
    setup = (ROOT/'scripts/setup-system-manager.sh').read_text()
    assert 'usermod -a -G dialout "${TARGET_USER}"' in setup
    assert 'esptool==4.11.0' in (ROOT/'services/system-manager/requirements.txt').read_text()
    sudoers = setup[setup.index('render_sudoers()'):setup.index('ensure_token_file()')]
    assert 'esptool' not in sudoers and 'flash' not in sudoers
    unit = (ROOT/'systemd/omk-system-manager.service.in').read_text()
    assert 'User=@OMK_USER@' in unit
    membership = setup.index('usermod -a -G dialout')
    assert membership < setup.index('systemctl restart "${SERVICE_NAME}"')
    assert membership < setup.index('systemctl start "${SERVICE_NAME}"')
    assert 'PrivateDevices=true' not in unit


@pytest.mark.parametrize('condition', ['STATUS_FAIL', 'STATUS_FAIL_AFTER_BUILD'])
def test_git_status_error_cannot_be_treated_as_clean(build, condition):
    run, root, event = build
    assert run(**{condition: '1'}).returncode != 0
    assert not (root/'firmware/esp32/omk-node/prebuilt/atom-s3-lite').exists()
    assert event.exists() == (condition == 'STATUS_FAIL_AFTER_BUILD')
