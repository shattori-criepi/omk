"""CLI adapter tests; all serial access and the setup operation are replaced."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'reinitialize_omk_node.py'
spec = importlib.util.spec_from_file_location('reinitialize_cli', SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture
def built(tmp_path):
    for filename, _, _ in cli.setup.SEGMENTS:
        (tmp_path/filename).write_bytes(b'synthetic build verify_reinitialize\x00')
    return tmp_path


def test_cli_uses_local_snapshot_and_shared_explicit_reset(built, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--reinitialize', '--device', '/dev/ttyACM0', '--build-dir', str(built)])
    monkeypatch.setattr(cli.setup, 'selected_device', lambda device: device)
    monkeypatch.setattr(cli.setup, 'read_mac', lambda *a: '02:00:00:00:00:01')
    calls = []
    def setup(device, node_id, confirmed, stage, *, package, reinitialize):
        assert device == '/dev/ttyACM0'
        assert node_id == cli.setup.node_id_from_mac('02:00:00:00:00:01')
        assert confirmed is True and reinitialize is True
        files = cli.setup.validate_package(package)
        assert files['firmware.bin'] == (built/'firmware.bin').read_bytes()
        assert package != built
        calls.append(package)
        stage('completed')
    monkeypatch.setattr(cli.setup, 'setup', setup)
    assert cli.main() == 0
    assert len(calls) == 1 and not calls[0].exists()
    assert 'Register attached sensors and Logical ID' in capsys.readouterr().out


def test_cli_requires_reset_option_before_touching_usb(built, monkeypatch):
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--device', '/dev/ttyACM0', '--build-dir', str(built)])
    monkeypatch.setattr(cli.setup, 'selected_device', lambda *a: pytest.fail('USB accessed'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_cli_rejects_invalid_build_before_usb_access(built, monkeypatch):
    (built/'firmware.bin').unlink()
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--reinitialize', '--device', '/dev/ttyACM0', '--build-dir', str(built)])
    monkeypatch.setattr(cli.setup, 'selected_device', lambda *a: pytest.fail('USB accessed'))
    with pytest.raises(OSError):
        cli.main()
