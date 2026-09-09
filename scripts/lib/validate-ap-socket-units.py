#!/usr/bin/env python3
"""Fail closed on source overrides AND systemd's effective/listening socket state."""
import pathlib
import re
import subprocess
import sys


def command(*args):
    return subprocess.check_output(args, text=True, timeout=10)


def entries(text):
    section = ''
    result = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.endswith('\\'):
            raise ValueError('continued directives are not permitted in OMK proxy units')
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        elif '=' in line:
            key, value = line.split('=', 1)
            result.append((section, key.strip(), value.strip()))
    return result


def source(directory, name, effective):
    if effective:
        return command('systemctl', 'cat', name)
    path = directory / name
    chunks = [path.read_text()]
    # Include adjacent unit-specific and type-wide drop-ins for offline checking.
    for subdir in (directory / (name.rsplit('.', 1)[1] + '.d'), directory / (name + '.d')):
        chunks.extend(p.read_text() for p in sorted(subdir.glob('*.conf')))
    return '\n'.join(chunks)


def exactly(items, section, key, value):
    values = [v for s, k, v in items if s == section and k == key]
    if values != [value]:
        raise ValueError(f'{key}: expected one {value!r}, got {values!r}')


def main():
    directory = pathlib.Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else '--source'
    if mode not in ('--source', '--effective', '--runtime'):
        raise ValueError('unknown validator mode')
    effective = mode != '--source'
    for component, port in (('dashboard', 8000), ('mqtt', 1883)):
        name = f'omk-{component}-ap-proxy'
        items = entries(source(directory, name + '.socket', effective))
        for key, value in [('FreeBind', 'yes'), ('BindToDevice', 'wlan0'), ('Accept', 'no'),
                           ('ListenStream', f'192.168.50.1:{port}')]:
            exactly(items, 'Socket', key, value)
        if any(k.startswith('Listen') and k != 'ListenStream' for s, k, v in items if s == 'Socket'):
            raise ValueError('additional listener type')
        service = entries(source(directory, name + '.service', effective))
        starts = [v for s, k, v in service if s == 'Service' and k == 'ExecStart']
        if len(starts) != 1 or not re.fullmatch(r'/[\w/.-]+/systemd-socket-proxyd 127\.0\.0\.1:' + str(port), starts[0]):
            raise ValueError('invalid proxy command/target')
        if effective:
            props = dict(line.split('=', 1) for line in command(
                'systemctl', 'show', name + '.socket', '--property=FreeBind,BindToDevice,Accept,Listen,ActiveState').splitlines() if '=' in line)
            expected = {'FreeBind': 'yes', 'BindToDevice': 'wlan0', 'Accept': 'no',
                        'Listen': f'192.168.50.1:{port} (Stream)'}
            if mode == '--runtime':
                expected['ActiveState'] = 'active'
            for key, value in expected.items():
                if props.get(key) != value:
                    raise ValueError(f'{name}: effective {key} is {props.get(key)!r}')
    if mode == '--runtime':
        rows = [line for line in command('ss', '-H', '-O', '-ltn4', '--inet-sockopt').splitlines() if len(line.split()) >= 4]
        listeners = [line.split()[3] for line in rows]
        for port in (8000, 1883):
            expected = f'192.168.50.1%wlan0:{port}'
            if listeners.count(expected) != 1:
                raise ValueError(f'actual wlan0-bound listener missing or duplicated: {expected}')
            row = next(line for line in rows if line.split()[3] == expected)
            if not re.search(r'inet-sockopt:\s*\([^)]*\bfreebind\b', row):
                raise ValueError(f'actual socket does not have IP_FREEBIND: {expected}')
            allowed = {expected, f'127.0.0.1:{port}'}
            if any(x.endswith(f':{port}') and x not in allowed for x in listeners):
                raise ValueError(f'unexpected IPv4 listener on {port}')
        if any(line.split()[3].endswith((':8000', ':1883')) for line in command('ss', '-H', '-ltn6').splitlines() if len(line.split()) >= 4):
            raise ValueError('unexpected IPv6 listener')
    print(f'PASS: AP proxy security ({mode[2:]})')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, IndexError) as error:
        print(f'FAIL: AP proxy security: {error}', file=sys.stderr)
        sys.exit(1)
