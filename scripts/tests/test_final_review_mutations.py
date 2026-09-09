"""Prove regression tests reject old bugs, editing disposable copies only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def worker_bug(text):
    start = text.index('  # --wait 0 only queues deactivation.')
    end = text.index('  bounded nmcli --wait 0 connection up uuid "${previous_uuid}"', start)
    return text[:start] + '''  current="$(current_uuid)" || return "${status}"
  if [[ -n "${current}" && "${current}" != -- ]]; then return "${status}"; fi
''' + text[end:]


def image_bug(text):
    return text.replace('    image: {saved["image"]}\\n', '')


def yaml_bug(text):
    return text.replace('except yaml.YAMLError:', 'except ValueError:')


def environment_bug(text):
    return 'import os\n' + text.replace("    host = ''", "    host = os.environ.get('MQTT_HOST', '')")


def rerun_bug(text):
    start = text.index('  if grep -Fxq soracom <<<"${ACTIVE_CONNECTIONS}"; then')
    text = text[:start] + text[start:].replace('    if [[ ! -e "${SORACOM_DISPATCHER_PATH}" ]]; then',
        '    was_present=no; [[ ! -e "${SORACOM_DISPATCHER_PATH}" ]] || was_present=yes\n    if [[ ! -e "${SORACOM_DISPATCHER_PATH}" ]]; then', 1)
    call = '    repair_dispatcher_routes_without_reconnect yes yes || fail "${EXIT_GENERAL}" \'Dispatcher route repair failed; existing routes were not replaced.\''
    return text.replace(call, '    if [[ "${was_present}" != yes ]]; then\n' + call + '\n    fi', 1)


cases = [
    ('async deactivation', 'scripts/omk-activate-access-point', worker_bug, 'test_activation_failure_injection.py', {'OMK_WORKER_TEST_MODES': 'delayed'}),
    ('old image pinning', 'scripts/lib/legacy-compose-ports.py', image_bug, 'test_ap_publish_migration.py', {'OMK_MIGRATION_TEST_MODES': 'health_fail'}),
    ('rollback health verification', 'scripts/lib/compose-ap-migration.sh', lambda t: t.replace('  verify_restored_production || return 1', '  :'), 'test_ap_publish_migration.py', {'OMK_MIGRATION_TEST_MODES': 'rollback_health_fail'}),
    ('YAML secret leak', 'scripts/lib/check-host-mqtt-config.py', yaml_bug, 'test_host_mqtt_persistence.py', {}),
    ('active SORACOM rerun', 'scripts/setup-soracom-onyx.sh', rerun_bug, 'test_soracom_preservation.py', {'OMK_SORACOM_TEST_MODES': 'rerun'}),
    ('setup environment masking', 'scripts/lib/check-host-mqtt-config.py', environment_bug, 'test_host_mqtt_persistence.py', {}),
]
for name, file, mutate, test, options in cases:
    with tempfile.TemporaryDirectory(prefix='omk-review-mutant-') as tmp:
        d = Path(tmp)
        shutil.copytree(ROOT/'scripts', d/'scripts')
        shutil.copytree(ROOT/'systemd', d/'systemd')
        shutil.copy2(ROOT/'compose.yaml', d/'compose.yaml')
        env = dict(os.environ, OMK_REVIEW_TEST_ROOT=str(d), **options)
        command = [sys.executable, str(ROOT/'scripts/tests'/test)]
        baseline = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        assert baseline.returncode == 0, (name, 'fixed baseline failed', baseline.stdout, baseline.stderr)
        path = d/file
        original = path.read_text()
        changed = mutate(original)
        assert changed != original, 'mutation did not apply: ' + name
        path.write_text(changed)
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode != 0, 'test accepted old bug: ' + name
        assert 'AssertionError' in result.stderr, (name, 'fixture did not reach assertions', result.stderr)
        print('PASS: regression test rejects restored old bug: ' + name, flush=True)
