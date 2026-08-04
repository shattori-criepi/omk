#!/usr/bin/env python3
"""Apply the provisional Phase 3B USB ESD D1 placement, and nothing else.

At 270 degrees, D1 pins 6 (USB_D+) and 4 (USB_D-) face the provisional J1
location below; pins 1 (USB_D+) and 3 (USB_D-) face U1 above.  This is a
temporary through-routing placement, not a completed USB layout.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


D1_FOOTPRINT = "Package_TO_SOT_SMD:SOT-23-6"
D1_X_MM = 100.0
D1_Y_MM = 99.0
D1_ROTATION_DEG = 270


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    d1 = next((fp for fp in board.GetFootprints() if fp.GetReference() == "D1"), None)
    if d1 is None:
        raise SystemExit("D1 not found; board was not modified")
    if d1.GetFPIDAsString() != D1_FOOTPRINT:
        raise SystemExit("D1 footprint mismatch; board was not modified")

    d1.SetOrientation(pcbnew.EDA_ANGLE(D1_ROTATION_DEG, pcbnew.DEGREES_T))
    d1.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(D1_X_MM), pcbnew.FromMM(D1_Y_MM)))
    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
