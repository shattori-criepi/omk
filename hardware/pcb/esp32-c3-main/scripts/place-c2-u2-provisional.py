#!/usr/bin/env python3
"""Apply the provisional Phase 3B C2/U2 placement, and nothing else.

This is the USB-clearance-prioritised candidate A.  C2 remains a marginal
shared D1.5/U2-VIN capacitor until the subsequent complete power-layout review.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


PLACEMENTS = {
    "U2": ("Package_TO_SOT_SMD:TSOT-23-6", 94.0, 94.0, 90),
    "C2": ("Capacitor_SMD:C_0402_1005Metric", 97.0, 98.0, 90),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    for reference, (footprint_id, x_mm, y_mm, rotation_deg) in PLACEMENTS.items():
        footprint = next((fp for fp in board.GetFootprints()
                          if fp.GetReference() == reference), None)
        if footprint is None:
            raise SystemExit(f"{reference} not found; board was not modified")
        if footprint.GetFPIDAsString() != footprint_id:
            raise SystemExit(f"{reference} footprint mismatch; board was not modified")

    for reference, (_, x_mm, y_mm, rotation_deg) in PLACEMENTS.items():
        footprint = next(fp for fp in board.GetFootprints() if fp.GetReference() == reference)
        footprint.SetOrientation(pcbnew.EDA_ANGLE(rotation_deg, pcbnew.DEGREES_T))
        footprint.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm)))

    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
