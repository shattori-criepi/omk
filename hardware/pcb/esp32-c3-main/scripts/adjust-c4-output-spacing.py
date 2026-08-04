#!/usr/bin/env python3
"""Increase provisional C4/C5 spacing without changing any other PCB item.

This is a Phase 3B placement-only adjustment.  It deliberately adds no
tracks, vias, zones, rules, or board-edge changes.  The resulting output loop,
return path, and sense route still require review after routing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


REFERENCE = "C4"
FOOTPRINT_ID = "Capacitor_SMD:C_0805_2012Metric"
TARGET_X_MM = 89.0
TARGET_Y_MM = 85.0
TARGET_ROTATION_DEG = 90


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    footprint = next((fp for fp in board.GetFootprints()
                      if fp.GetReference() == REFERENCE), None)
    if footprint is None or footprint.GetFPIDAsString() != FOOTPRINT_ID:
        raise SystemExit("C4 validation failed; board was not modified")

    target = pcbnew.VECTOR2I(pcbnew.FromMM(TARGET_X_MM), pcbnew.FromMM(TARGET_Y_MM))
    target_angle = pcbnew.EDA_ANGLE(TARGET_ROTATION_DEG, pcbnew.DEGREES_T)
    if footprint.GetPosition() != target or footprint.GetOrientation() != target_angle:
        footprint.SetOrientation(target_angle)
        footprint.SetPosition(target)
        pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
