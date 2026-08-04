#!/usr/bin/env python3
"""Place U1 and add provisional Phase 3B board/antenna planning geometry.

This script intentionally changes only U1, four provisional Edge.Cuts lines, and
non-manufacturing Dwgs.User antenna-clearance guide geometry.  The 60 x 60 mm
outline is a planning placeholder, not a released board size.  It does not
modify the antenna keepout rule area embedded in U1's footprint.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew


# Provisional Phase 3B planning coordinates (millimetres); not a final outline.
BOARD_LEFT_MM = 70.0
BOARD_TOP_MM = 50.0
BOARD_RIGHT_MM = 130.0
BOARD_BOTTOM_MM = 110.0

# ESP32-C3-MINI-1 local module top is y=-8.3 mm at 0 degrees.  Keep its antenna
# (negative-Y side) pointing upwards and make the module top coincident with the
# provisional board top edge.
U1_X_MM = 100.0
U1_MODULE_TOP_LOCAL_Y_MM = -8.3
U1_Y_MM = BOARD_TOP_MM - U1_MODULE_TOP_LOCAL_Y_MM

# This is a visual-only external clearance guide, outside the board.  It is not
# a rule area and intentionally imposes no DRC constraint.
ANTENNA_GUIDE_LEFT_MM = U1_X_MM - 6.6
ANTENNA_GUIDE_RIGHT_MM = U1_X_MM + 6.6
ANTENNA_GUIDE_TOP_MM = BOARD_TOP_MM - 15.0
ANTENNA_GUIDE_BOTTOM_MM = BOARD_TOP_MM
GUIDE_TEXT = "ANTENNA EXTERNAL CLEARANCE 15 mm - GUIDE ONLY"

EDGE_WIDTH_MM = 0.1
GUIDE_WIDTH_MM = 0.25


def mm_point(x_mm: float, y_mm: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm))


def same_point(a: pcbnew.VECTOR2I, b: pcbnew.VECTOR2I) -> bool:
    return a.x == b.x and a.y == b.y


def has_segment(board: pcbnew.BOARD, layer: int, start: pcbnew.VECTOR2I,
                end: pcbnew.VECTOR2I) -> bool:
    for item in board.GetDrawings():
        if not isinstance(item, pcbnew.PCB_SHAPE) or item.GetLayer() != layer:
            continue
        if item.GetShape() != pcbnew.S_SEGMENT:
            continue
        if ((same_point(item.GetStart(), start) and same_point(item.GetEnd(), end))
                or (same_point(item.GetStart(), end) and same_point(item.GetEnd(), start))):
            return True
    return False


def ensure_segment(board: pcbnew.BOARD, layer: int, start: pcbnew.VECTOR2I,
                   end: pcbnew.VECTOR2I, width_mm: float) -> None:
    if has_segment(board, layer, start, end):
        return
    item = pcbnew.PCB_SHAPE(board)
    item.SetShape(pcbnew.S_SEGMENT)
    item.SetLayer(layer)
    item.SetStart(start)
    item.SetEnd(end)
    item.SetWidth(pcbnew.FromMM(width_mm))
    board.Add(item)


def has_guide_text(board: pcbnew.BOARD) -> bool:
    for item in board.GetDrawings():
        if not isinstance(item, pcbnew.PCB_TEXT):
            continue
        if item.GetLayer() == pcbnew.Dwgs_User and item.GetText() == GUIDE_TEXT:
            return True
    return False


def ensure_guide_text(board: pcbnew.BOARD) -> None:
    if has_guide_text(board):
        return
    text = pcbnew.PCB_TEXT(board)
    text.SetText(GUIDE_TEXT)
    text.SetLayer(pcbnew.Dwgs_User)
    text.SetPosition(mm_point(U1_X_MM, ANTENNA_GUIDE_TOP_MM - 1.5))
    text.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.0), pcbnew.FromMM(1.0)))
    text.SetTextThickness(pcbnew.FromMM(0.15))
    board.Add(text)


def ensure_rectangle(board: pcbnew.BOARD, layer: int, left: float, top: float,
                     right: float, bottom: float, width_mm: float) -> None:
    top_left = mm_point(left, top)
    top_right = mm_point(right, top)
    bottom_right = mm_point(right, bottom)
    bottom_left = mm_point(left, bottom)
    for start, end in ((top_left, top_right), (top_right, bottom_right),
                       (bottom_right, bottom_left), (bottom_left, top_left)):
        ensure_segment(board, layer, start, end, width_mm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, help="path to .kicad_pcb")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.board))
    u1 = next((fp for fp in board.GetFootprints() if fp.GetReference() == "U1"), None)
    if u1 is None:
        raise SystemExit("U1 not found; board was not modified")
    if u1.GetFPIDAsString() != "Espressif:ESP32-C3-MINI-1":
        raise SystemExit("U1 footprint mismatch; board was not modified")

    u1.SetOrientation(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
    u1.SetPosition(mm_point(U1_X_MM, U1_Y_MM))

    ensure_rectangle(board, pcbnew.Edge_Cuts, BOARD_LEFT_MM, BOARD_TOP_MM,
                     BOARD_RIGHT_MM, BOARD_BOTTOM_MM, EDGE_WIDTH_MM)
    ensure_rectangle(board, pcbnew.Dwgs_User, ANTENNA_GUIDE_LEFT_MM,
                     ANTENNA_GUIDE_TOP_MM, ANTENNA_GUIDE_RIGHT_MM,
                     ANTENNA_GUIDE_BOTTOM_MM, GUIDE_WIDTH_MM)
    ensure_guide_text(board)

    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
