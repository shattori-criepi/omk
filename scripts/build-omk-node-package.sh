#!/usr/bin/env bash
# Run from the repository scripts directory; no USB access.
set -euo pipefail
[[ $# -eq 0 ]] || { echo 'usage: build-omk-node-package.sh' >&2; exit 2; }
root="$(git rev-parse --show-toplevel)"
repository_status="$(git -C "$root" status --porcelain --untracked-files=all)"
[[ -z "$repository_status" ]] || { echo 'A clean repository is required.' >&2; exit 1; }
source_commit="$(git -C "$root" rev-parse HEAD)"
project="$root/firmware/esp32/omk-node"
"${PLATFORMIO_CMD:-pio}" run -d "$project" -e atom-s3-lite -t clean
"${PLATFORMIO_CMD:-pio}" run -d "$project" -e atom-s3-lite
# A concurrent source edit must not be attributed to the original HEAD.
current_commit="$(git -C "$root" rev-parse HEAD)"
repository_status="$(git -C "$root" status --porcelain --untracked-files=all)"
[[ "$source_commit" == "$current_commit" && -z "$repository_status" ]] || { echo 'Repository changed during build.' >&2; exit 1; }
python3 - "$project" "$source_commit" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
project = Path(sys.argv[1])
destination = project / 'prebuilt/atom-s3-lite'
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
    stage = Path(temporary)
    segments = []
    for filename, offset in [('bootloader.bin', '0x00000000'), ('partitions.bin', '0x00008000'), ('firmware.bin', '0x00010000')]:
        source = project / '.pio/build/atom-s3-lite' / filename
        if source.is_symlink() or not source.is_file():
            raise SystemExit('Invalid build artifact')
        shutil.copyfile(source, stage / filename)
        data = (stage / filename).read_bytes()
        segments.append(dict(filename=filename, offset=offset, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    (stage / 'manifest.json').write_text(json.dumps(dict(schema_version=1, target='atom-s3-lite', chip='esp32s3', source_commit=sys.argv[2], segments=segments), indent=2) + '\n')
    destination.mkdir(exist_ok=True)
    for path in stage.iterdir():
        path.replace(destination / path.name)
PY
