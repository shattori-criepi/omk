#!/usr/bin/env python3
"""Apply provisional Phase 3B C1/C3 positions, and nothing else.

The positions preserve working room for L1 and keep the input and bootstrap
loops marked MARGINAL pending complete power-block placement and routing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


PLACEMENTS = {
    "C1": ("Capacitor_SMD:C_0805_2012Metric", 90.0, 96.0, 90),
    "C3": ("Capacitor_SMD:C_0402_1005Metric", 96.5, 91.0, 0),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))

    for reference, (footprint_id, _, _, _) in PLACEMENTS.items():
        footprint = next((fp for fp in board.GetFootprints()
                          if fp.GetReference() == reference), None)
        if footprint is None or footprint.GetFPIDAsString() != footprint_id:
            raise SystemExit(f"{reference} validation failed; board was not modified")

    for reference, (_, x_mm, y_mm, rotation_deg) in PLACEMENTS.items():
        footprint = next(fp for fp in board.GetFootprints() if fp.GetReference() == reference)
        footprint.SetOrientation(pcbnew.EDA_ANGLE(rotation_deg, pcbnew.DEGREES_T))
        footprint.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm)))

    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
