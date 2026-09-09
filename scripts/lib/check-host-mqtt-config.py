#!/usr/bin/env python3
"""Detect a legacy local AP broker without displaying configuration or secrets."""
from pathlib import Path
import sys

try:
    path = Path(sys.argv[1])
    # The installed services do not inherit the setup shell's MQTT variables.
    host = ''
    if path.is_file():
        if path.suffix in ('.yaml', '.yml'):
            import yaml  # B-route's installed runtime dependency
            try:
                config = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError:
                sys.exit('ERROR: MQTT configuration YAML is invalid.')
            host = host or str(config.get('mqtt', {}).get('host', ''))
        else:
            for line in path.read_text().splitlines():
                key, separator, value = line.partition('=')
                if separator and key.strip() == 'MQTT_HOST':
                    host = value.strip().strip('\"\'')
    if host == '192.168.50.1':
        sys.exit('FAIL: legacy Gateway MQTT host detected; change mqtt.host/MQTT_HOST to 127.0.0.1 before restarting this service. Node broker settings must remain unchanged.')
except (OSError, ValueError, TypeError, AttributeError):
    sys.exit('FAIL: cannot validate Gateway MQTT configuration; no configuration contents were logged.')
