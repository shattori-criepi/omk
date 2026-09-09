"""Check persistent settings and the actual setup logging/call boundary."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(os.environ.get('OMK_REVIEW_TEST_ROOT', Path(__file__).resolve().parents[2]))
checker = ROOT / 'scripts/lib/check-host-mqtt-config.py'
secret = 'FixtureSecretDoNotLog123'
with tempfile.TemporaryDirectory(prefix='omk-mqtt-settings-') as tmp:
    d = Path(tmp)
    config = d/'services/broute-meter/config/settings.yaml'
    config.parent.mkdir(parents=True)
    env = dict(os.environ, MQTT_HOST='127.0.0.1', MQTT_PORT='1883', FIXTURE=str(d),
               REVIEW_SETUP=str(ROOT/'scripts/setup-broute-meter.sh'), REVIEW_PYTHON=sys.executable)
    cases = [('malformed', 'mqtt:\n  password: ['+secret+'\n', False),
             ('legacy', 'mqtt:\n  host: 192.168.50.1\n', False),
             ('external', 'mqtt:\n  host: broker.example.net\n', True),
             ('loopback', 'mqtt:\n  host: 127.0.0.1\n', True)]
    for name, content, accepted in cases:
        config.write_text(content)
        result = subprocess.run([sys.executable, str(checker), str(config)], env=env, capture_output=True, text=True)
        assert (result.returncode == 0) == accepted, (name, result.returncode)
        if secret in result.stdout + result.stderr:
            raise AssertionError('checker leaked the secret')
        assert config.read_text() == content, 'checker modified user configuration'
        if name == 'malformed':
            assert result.stderr == 'ERROR: MQTT configuration YAML is invalid.\n'
        if accepted:
            continue
        # Invoke the real main function, including its tee-backed log and checker.
        # Replace only unrelated install operations; any service operation is fatal.
        shell = r'''
source "$REVIEW_SETUP"
OMK_ROOT="$FIXTURE"
LOG_DIR="$FIXTURE/logs"
VENV_PYTHON="$REVIEW_PYTHON"
initialize_target_identity() { :; }
preflight() { :; }
ensure_log_directory() { mkdir -p "$LOG_DIR"; }
migrate_legacy_runtime_config() { :; }
ensure_python_runtime() { :; }
for operation in ensure_runtime_directory ensure_credentials_permissions install_helper install_vbus_helper install_sudoers install_unit ensure_service_state verify_installation; do
  eval "$operation() { touch \"\$FIXTURE/forbidden\"; exit 99; }"
done
main
'''
        result = subprocess.run(['bash', '-c', shell], env=env, capture_output=True, text=True)
        assert result.returncode == 1, 'setup failed to reject configuration before service changes'
        assert not (d/'forbidden').exists(), 'setup reached service mutation'
        logs = ''.join(p.read_text() for p in (d/'logs').glob('*.log'))
        if secret in result.stdout + result.stderr + logs:
            raise AssertionError('setup log/stdout/stderr leaked the secret')
        assert ('YAML is invalid' if name == 'malformed' else 'legacy Gateway MQTT host detected') in logs
print('PASS: malformed YAML stays secret; persistent B-route settings override setup-only environment; external broker preserved.')
