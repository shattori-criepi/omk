#!/usr/bin/env python3
"""Move every non-U1 footprint as one rigid group into a temporary staging area.

This is not component placement.  It preserves each non-U1 footprint's rotation
and every relative X/Y offset, leaves U1 and all board geometry untouched, and
places the group at least 15 mm right of the provisional board edge.  The
staging position is solely for Phase 3B working space.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


PROVISIONAL_BOARD_RIGHT_MM = 130.0
STAGING_GAP_MM = 15.0  # Deliberately exceeds the required 10 mm minimum.
STAGING_LEFT_MM = PROVISIONAL_BOARD_RIGHT_MM + STAGING_GAP_MM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    u1 = next((fp for fp in board.GetFootprints() if fp.GetReference() == "U1"), None)
    if u1 is None:
        raise SystemExit("U1 not found; board was not modified")

    staged = [fp for fp in board.GetFootprints() if fp.GetReference() != "U1"]
    if len(staged) != 40:
        raise SystemExit(f"expected 40 non-U1 footprints, found {len(staged)}; board was not modified")

    current_left = min(fp.GetBoundingBox().GetX() for fp in staged)
    target_left = pcbnew.FromMM(STAGING_LEFT_MM)

    # Idempotence: once the complete group has reached the staging boundary,
    # do not apply a second translation.
    if current_left < target_left:
        delta = pcbnew.VECTOR2I(target_left - current_left, 0)
        for footprint in staged:
            footprint.Move(delta)

    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
