#!/usr/bin/env python3
"""Apply the provisional Phase 3B USB-C J1 placement, and nothing else.

The placement uses the temporary board-edge convention that J1's F.Fab positive
Y edge lies on the provisional board bottom.  It is not a final mechanical
placement: JAE's official board-edge, overhang, and mating-envelope data still
need verification.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


J1_FOOTPRINT = "Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500"
J1_X_MM = 100.0
J1_Y_MM = 106.4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    j1 = next((fp for fp in board.GetFootprints() if fp.GetReference() == "J1"), None)
    if j1 is None:
        raise SystemExit("J1 not found; board was not modified")
    if j1.GetFPIDAsString() != J1_FOOTPRINT:
        raise SystemExit("J1 footprint mismatch; board was not modified")

    # 0 degrees keeps the assumed mating/opening side on positive Y (downward).
    j1.SetOrientation(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
    j1.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(J1_X_MM), pcbnew.FromMM(J1_Y_MM)))
    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
