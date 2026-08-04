#!/usr/bin/env python3
"""Apply provisional Phase 3B L1/C4/C5 positions, and nothing else.

This is the USB-clearance-prioritised output-block candidate. SW/output/sense
loop assessments remain MARGINAL until copper, return paths, and stack-up are
reviewed; this script does not add any of those items.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


PLACEMENTS = {
    "L1": ("Inductor_SMD:L_Coilcraft_XxL4020", 92.815, 89.3, 180),
    "C4": ("Capacitor_SMD:C_0805_2012Metric", 89.0, 85.5, 90),
    "C5": ("Capacitor_SMD:C_0805_2012Metric", 89.0, 89.2, 90),
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
