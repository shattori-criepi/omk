"""Exercise setup's actual venv creation blocks against interrupted environments."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['data-transformer', 'data-exporter', 'broute-meter', 'ichijo-energy-node'])
def test_partial_venv_is_repaired_without_clearing(name, tmp_path):
    source = (ROOT / f'scripts/setup-{name}.sh').read_text()
    if name == 'data-exporter':
        block = source.split('VENV="${OMK_ROOT}/services/data-exporter/.venv"\n', 1)[1].split('"${AS_TARGET[@]}" "${VENV}/bin/python" -m pip', 1)[0]
    else:
        variable = 'VENV_PATH' if name == 'broute-meter' else 'VENV'
        start = source.index(f'if [[ ! -d "${{{variable}}}" ]]')
        end = source.index('if [[ ! -x "${VENV', start) if name == 'data-transformer' else source.index('[[ -x "${VENV', start)
        block = source[start:end]
    venv = tmp_path / 'partial'
    # A killed venv process can leave a directory but no Python or pip.
    venv.mkdir()
    sentinel = venv / 'preserve-me'
    sentinel.write_text('existing state')
    script = '''set -euo pipefail
VENV="$1" VENV_PATH="$1" TARGET_USER="$(id -un)"
AS_TARGET=() SUDO=(as_target)
as_target() { shift 2; "$@"; }
log() { :; }
'''+block
    subprocess.run(['bash', '-c', script, 'test', str(venv)], check=True)
    subprocess.run([str(venv / 'bin/python'), '-m', 'pip', '--version'], check=True, capture_output=True)
    assert sentinel.read_text() == 'existing state'
    # The same setup block must also preserve a completed environment.
    subprocess.run(['bash', '-c', script, 'test', str(venv)], check=True)
    assert sentinel.read_text() == 'existing state'
