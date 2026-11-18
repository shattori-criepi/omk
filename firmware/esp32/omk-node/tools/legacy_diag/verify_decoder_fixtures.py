#!/usr/bin/env python3
"""Offline Gateway acceptance check. Never opens a serial port or arms a node."""
import argparse
import hashlib
import json
from pathlib import Path

import diag_flash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixtures', type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.fixtures / 'manifest.json').read_text())
    for name, digest in manifest['tools'].items():
        assert hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest, name
    for name, expected in manifest['fixtures'].items():
        raw = (args.fixtures / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected['sha256'], name
        decoded = diag_flash.decode_flash(raw)
        assert decoded['size_valid'], name
        for slot, reference in zip(decoded['slots'], expected['slots'], strict=True):
            assert slot['state'] == reference['state'], (name, slot['slot'])
            if reference['state'] == 'valid':
                assert 'payload_decode_error' not in slot, slot
                actual = slot['diagnostic']
                assert all(actual[k] == v for k, v in reference['diagnostic'].items()), name
        print(f'PASS {name}: ' + ', '.join(s['state'] for s in decoded['slots']))
    print('PASS collector/decoder SHA256 and schema 1 / original schema 2 / LR2R1 schema 2')
    print('Offline compatibility only; serial transport and physical Flash persistence remain hardware acceptance items.')


if __name__ == '__main__':
    main()
