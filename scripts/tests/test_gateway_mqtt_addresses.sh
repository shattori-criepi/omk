#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
python3 - "${ROOT_DIR}" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tempfile
root=Path(sys.argv[1])
# Explicit service scope separates host clients from Node provisioning addresses
# and Compose-internal clients, which correctly use the service name mosquitto.
for service in ('ichijo-energy-node','broute-meter','ble-sensor-manager'):
    for path in (root/'services'/service).rglob('*'):
        if not path.is_file() or any(part in ('.venv','tests','__pycache__','.pytest_cache') for part in path.parts): continue
        if path.suffix in ('.py','.yaml','.yml','.example') or path.name=='.env.example':
            assert '192.168.50.1' not in path.read_text(), f'Legacy Gateway broker remains: {path}'
for path in (root/'systemd').glob('*.in'):
    assert 'MQTT_HOST=192.168.50.1' not in path.read_text(), path
assert 'mqtt://192.168.50.1:1883' in (root/'firmware/esp32/omk-node/src/mqtt_registration.c').read_text()
checker=root/'scripts/lib/check-host-mqtt-config.py'
with tempfile.TemporaryDirectory() as tmp:
    for name,content in [('settings.yaml','mqtt:\n  host: "192.168.50.1"\n'),('ichijo.env','MQTT_HOST=192.168.50.1\n')]:
        p=Path(tmp)/name; p.write_text(content+'# SecretMustNotAppear123\n')
        env=dict(os.environ); env.pop('MQTT_HOST',None)
        result=subprocess.run([sys.executable,str(checker),str(p)],env=env,capture_output=True,text=True)
        assert result.returncode!=0, 'legacy settings silently accepted'
        assert 'SecretMustNotAppear123' not in result.stdout+result.stderr
        p.write_text(content.replace('192.168.50.1','127.0.0.1'))
        subprocess.run([sys.executable,str(checker),str(p)],env=env,check=True)
print('PASS: Gateway/Node MQTT separation and legacy B-route/Ichijo detection.')
PY
