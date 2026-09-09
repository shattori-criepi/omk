#!/usr/bin/env python3
"""Snapshot running production image IDs and old publishes, never environment/secrets."""
import json
from pathlib import Path
import re
import sys

known = {'omk-' + s: s for s in ('mosquitto', 'dashboard', 'sensor-collector', 'harvest-uploader')}
services = {}
legacy = False
for container in json.load(sys.stdin):
    name = container['Name'].lstrip('/')
    if name not in known or not container['State']['Running']:
        continue
    service = known[name]
    image = container['Image']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        sys.exit('Refusing rollback without an immutable production image ID')
    ports = []
    port = {'mosquitto': 1883, 'dashboard': 8000}.get(service)
    for target, addresses in (container['HostConfig'].get('PortBindings') or {}).items():
        for entry in addresses or []:
            if not port or target != f'{port}/tcp' or entry['HostIp'] not in ('127.0.0.1', '192.168.50.1') or str(entry['HostPort']) != str(port):
                sys.exit('Refusing migration of unexpected production port bindings')
            legacy |= entry['HostIp'] == '192.168.50.1'
            ports.append(f"{entry['HostIp']}:{port}:{port}")
    if port and not ports:
        sys.exit('Refusing migration of a production backend without its expected publish')
    services[service] = {'image': image, 'ports': ports,
                         'host': '127.0.0.1' if any(p.startswith('127.0.0.1:') for p in ports) else '192.168.50.1'}
if legacy:
    Path(sys.argv[1]).write_text(json.dumps(services))
    print('services:')
    for service, saved in services.items():
        print(f'  {service}:\n    image: {saved["image"]}\n    pull_policy: never')
        if saved['ports']:
            print('    ports: !override')
            for port in saved['ports']:
                print('      - ' + json.dumps(port))
