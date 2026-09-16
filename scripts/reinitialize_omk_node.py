#!/usr/bin/env python3
"""Explicit destructive re-setup using the same implementation as Dashboard.

Called by flash-omk-node.sh after a source build. The private package is local
and temporary, not a published release attributed to a clean source commit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/system-manager/src'))
from omk_system_manager import node_setup as setup
from omk_system_manager.node_provisioning import ProvisioningError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reinitialize', action='store_true', required=True,
                        help='Replace PoP, clear Logical ID/old Wi-Fi, and configure this Gateway AP')
    parser.add_argument('--device', required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='omk-local-package-') as temporary:
        package = Path(temporary)
        segments = []
        for filename, offset, maximum in setup.SEGMENTS:
            content = setup.regular_bytes(args.build_dir / filename, maximum)
            (package / filename).write_bytes(content)
            segments.append(dict(filename=filename, offset=offset, size=len(content),
                                 sha256=hashlib.sha256(content).hexdigest()))
        (package / 'manifest.json').write_text(json.dumps(dict(
            schema_version=1, target='atom-s3-lite', chip='esp32s3',
            source_commit='0' * 40, local_build=True, segments=segments)))
        setup.validate_package(package)
        device = setup.selected_device(args.device)
        node_id = setup.node_id_from_mac(setup.read_mac(device))
        print(f'Reinitializing Node {node_id}. Keep this device connected.', flush=True)
        setup.setup(device, node_id, True, lambda stage: print(stage, flush=True),
                    package=package, reinitialize=True)
    print(f'Node {node_id} provisioned. Register attached sensors and Logical ID in Dashboard.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ProvisioningError as error:
        print(f"Re-setup failed ({error.code}). Keep the saved credential and retry with --reinitialize.", file=sys.stderr)
        raise SystemExit(1)
    except OSError:
        # No exception details: USB requests and filesystem data can contain secrets.
        print('Re-setup failed. Keep the saved credential and retry with --reinitialize. Check system-manager setup prerequisites and USB connection.', file=sys.stderr)
        raise SystemExit(1)
