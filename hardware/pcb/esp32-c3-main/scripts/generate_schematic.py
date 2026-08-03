#!/usr/bin/env python3
"""Generate the OMK ESP32-C3 MAIN Rev.A Phase 2 schematic with KiCad APIs."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import kicad_sch_api as ksa
from kicad_sch_api.library.cache import SymbolLibraryCache, set_symbol_cache

ROOT = Path(__file__).resolve().parents[1]
BLANK = ROOT / "esp32-c3-main.blank.kicad_sch"
GENERATED = ROOT / "esp32-c3-main.generated.kicad_sch"
FORMAL = ROOT / "esp32-c3-main.kicad_sch"
ERC = ROOT / "erc-report.txt"
PDF = ROOT / "esp32-c3-main-schematic.pdf"
STANDARD_SYMBOLS = Path(os.environ["KICAD_SYMBOL_DIR"])
# kicad-sch-api 0.5.6 writes invalid embedded symbols when fed the current
# KiCad 9/10 library syntax.  Espressif.kicad_sym is from the official
# Espressif KiCad 7 branch; the newer source is preserved alongside it.
ESPRESSIF_SYMBOLS = ROOT / "symbols" / "Espressif.kicad_sym"
CACHE_DIR = ROOT / ".cache" / "kicad-sch-api"

# A3 landscape coordinate system (420 x 297 mm).  The bottom-right title
# block, including its margin, is deliberately unavailable to circuitry.
PAGE_SIZE = (420.0, 297.0)
TITLE_BLOCK_RESERVED = (295.0, 225.0, 420.0, 297.0)
PAGE_MARGIN = 12.0

# Functional blocks flow left-to-right.  These reservation rectangles are
# intentionally larger than their symbols and visible fields; they make a
# generator failure preferable to silently producing overlapping blocks.
BLOCK_RECTS = {
    "usb": (20.0, 30.0, 120.0, 130.0),
    "power": (130.0, 30.0, 230.0, 105.0),
    "mcu": (125.0, 105.0, 225.0, 215.0),
    "reset_boot": (25.0, 155.0, 115.0, 220.0),
    "i2c": (235.0, 100.0, 290.0, 185.0),
    "uart_led": (235.0, 185.0, 290.0, 220.0),
}

# Pin sets are checked against the installed KiCad libraries before generation.
EXPECTED_PINS = {
    "Connector:USB_C_Receptacle_USB2.0_16P": {"S1","A1","A12","B1","B12","A4","A9","B4","B9","A5","B5","A7","B7","A6","B6","A8","B8"},
    "Espressif:ESP32-C3-MINI-1": {str(i) for i in range(1, 54)},
    "Regulator_Switching:AP63203WU": {"1","2","3","4","5","6"},
    "Connector_Generic:Conn_01x04": {"1","2","3","4"},
    "Device:R": {"1","2"}, "Device:C": {"1","2"}, "Device:L": {"1","2"},
    "Device:Polyfuse": {"1","2"}, "Switch:SW_Push": {"1","2"},
    "Connector:TestPoint": {"1"}, "Device:LED": {"1","2"},
    "Power_Protection:USBLC6-2SC6": {"1","2","3","4","5","6"},
    "power:GND": {"1"}, "power:PWR_FLAG": {"1"},
}


def prepare_cache() -> None:
    cache = SymbolLibraryCache(cache_dir=CACHE_DIR, enable_persistence=True)
    cache.discover_libraries([STANDARD_SYMBOLS])
    cache.add_library_path(ESPRESSIF_SYMBOLS)
    set_symbol_cache(cache)


def validate_symbols_and_pins() -> None:
    """Fail closed if any source symbol or official ESP32 pin set changes."""
    prepare_cache()
    for lib_id, expected in EXPECTED_PINS.items():
        symbol = ksa.get_symbol_info(lib_id)
        if symbol is None:
            raise RuntimeError(f"Missing symbol: {lib_id}")
        actual = {pin["number"] for pin in symbol.list_pins()}
        if actual != expected:
            raise RuntimeError(f"Pin mismatch for {lib_id}: {sorted(actual)}")


def load_or_create_schematic() -> ksa.Schematic:
    schematic = ksa.load_schematic(BLANK)
    if len(schematic.components) or len(schematic.wires) or len(schematic.labels):
        raise RuntimeError("The preserved template must be blank")
    return schematic


def add(s: ksa.Schematic, lib: str, ref: str, value: str, pos: tuple[float, float], fp: str = "", mpn: str = "", **props):
    fields = dict(props)
    if mpn:
        fields["Manufacturer Part Number"] = mpn
    # kicad-sch-api 0.5.6 has incorrect pin placement at 90/270 degrees.
    component = s.components.add(lib, reference=ref, value=value, position=pos, rotation=0, footprint=fp or None, **fields)
    # Keep sourcing information in the symbol but show only Reference/Value.
    component.hidden_properties.update({"Footprint", "Datasheet", *fields.keys()})
    return component


def labels(s: ksa.Schematic, net: str, pins: list[tuple[str, str]]) -> None:
    for ref, pin in pins:
        s.add_label(net, pin=(ref, pin))


def right_side_labels_with_stubs(s: ksa.Schematic, nets: list[tuple[str, str, float]]) -> None:
    """Place U1 right-side labels after a short horizontal wire and stagger them."""
    for pin, net, y_offset in nets:
        pin_pos = s.get_component_pin_position("U1", pin)
        if pin_pos is None:
            raise RuntimeError(f"No position for U1.{pin}")
        # 7.62 mm is six KiCad 50-mil grid steps.
        elbow = (pin_pos.x + 7.62, pin_pos.y)
        label_pos = (pin_pos.x + 7.62, pin_pos.y + y_offset)
        s.wires.add(start=(pin_pos.x, pin_pos.y), end=elbow)
        if y_offset:
            s.wires.add(start=elbow, end=label_pos)
        s.add_label(net, position=label_pos, rotation=0)


def no_connect(s: ksa.Schematic, ref: str, pins: list[str]) -> None:
    for pin in pins:
        point = s.get_component_pin_position(ref, pin)
        if point is None:
            raise RuntimeError(f"No position for {ref}.{pin}")
        s.no_connects.add(point)


def add_title_block(s: ksa.Schematic) -> None:
    s.set_paper_size("A3")
    s.set_title_block(title="OMK ESP32-C3 MAIN", rev="Rev.A", company="OMK")


def add_design_notes(s: ksa.Schematic) -> None:
    """Keep short review notes in unused space, never over a circuit block."""
    for text, position in (
        ("R3: USB shield option, DNP", (260, 30)),
        ("R6/R7: optional I2C pull-ups, DNP", (260, 37)),
        ("D2/R8: status LED, DNP", (260, 44)),
        ("L1: final MPN and saturation current TBD", (260, 51)),
        ("J1: final MPN TBD; J3: JST PH selected", (260, 58)),
    ):
        s.add_text(text, position=position, size=1.27)


def validate_layout(s: ksa.Schematic) -> None:
    """Reject placements outside the A3 drawing area or into the title block."""
    x1, y1, x2, y2 = TITLE_BLOCK_RESERVED
    placed: list[tuple[str, float, float]] = []
    for component in s.components:
        x, y = component.position.x, component.position.y
        if not (PAGE_MARGIN <= x <= PAGE_SIZE[0] - PAGE_MARGIN and PAGE_MARGIN <= y <= PAGE_SIZE[1] - PAGE_MARGIN):
            raise RuntimeError(f"{component.reference} is outside the A3 drawing area")
        if x1 <= x <= x2 and y1 <= y <= y2:
            raise RuntimeError(f"{component.reference} intrudes into the title-block reservation")
        # A 5 mm center-to-center guard catches accidentally stacked symbols,
        # including test points, without rejecting deliberate pin-adjacent text.
        for other_ref, other_x, other_y in placed:
            if abs(x - other_x) < 5.0 and abs(y - other_y) < 5.0:
                raise RuntimeError(f"Symbols overlap: {component.reference} / {other_ref}")
        placed.append((component.reference, x, y))
    for label in s.labels:
        if x1 <= label.position.x <= x2 and y1 <= label.position.y <= y2:
            raise RuntimeError(f"Net label {label.text} intrudes into the title-block reservation")
    for text in s.texts:
        if x1 <= text.position.x <= x2 and y1 <= text.position.y <= y2:
            raise RuntimeError(f"Design note intrudes into the title-block reservation")
    names = list(BLOCK_RECTS)
    for index, name in enumerate(names):
        ax1, ay1, ax2, ay2 = BLOCK_RECTS[name]
        for other in names[index + 1:]:
            bx1, by1, bx2, by2 = BLOCK_RECTS[other]
            if max(ax1, bx1) < min(ax2, bx2) and max(ay1, by1) < min(ay2, by2):
                raise RuntimeError(f"Layout blocks overlap: {name} / {other}")


def add_usb_c_block(s: ksa.Schematic) -> None:
    add(s, "Connector:USB_C_Receptacle_USB2.0_16P", "J1", "USB-C", (45, 75), "Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500", "JAE DX07S016JA1R1500", LCSC="TBD")
    add(s, "Device:Polyfuse", "F1", "PTC 1.10A", (112, 55), "Fuse:Fuse_1812_4532Metric", "Bourns MF-MSMF110-2", Status="Candidate; JLC verification required")
    for ref, x in (("R1", 78), ("R2", 92)):
        add(s, "Device:R", ref, "5.1k", (x, 98), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402FR-075K1L")
    add(s, "Device:R", "R3", "0R DNP", (75, 116), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402JR-070RL", Status="DNP")
    add(s, "Power_Protection:USBLC6-2SC6", "D1", "USBLC6-2SC6", (105, 112), "Package_TO_SOT_SMD:SOT-23-6", "STMicroelectronics USBLC6-2SC6", LCSC="C7519")
    add(s, "Device:C", "C1", "10uF", (140, 55), "Capacitor_SMD:C_0805_2012Metric", "Murata GRM21BR61A106KE19L")
    add(s, "Device:C", "C2", "100nF", (150, 55), "Capacitor_SMD:C_0402_1005Metric", "Murata GRM155R71E104KE14D")
    add(s, "power:PWR_FLAG", "#FLG01", "PWR_FLAG", (68, 45))
    add(s, "power:GND", "#PWR01", "GND", (68, 105))
    add(s, "power:PWR_FLAG", "#FLG02", "PWR_FLAG", (58, 105))
    # These are intentionally local wires only: they establish the USB source,
    # shield option, and power flags without any page-spanning wiring.
    s.add_wire_between_pins("J1", "A4", "F1", "1")
    s.add_wire_between_pins("F1", "1", "#FLG01", "1")
    s.add_wire_between_pins("J1", "A1", "#PWR01", "1")
    s.add_wire_between_pins("#PWR01", "1", "#FLG02", "1")
    s.add_wire_between_pins("J1", "S1", "R3", "1")
    s.add_wire_between_pins("R3", "2", "#PWR01", "1")
    labels(s, "+5V_USB", [("J1", p) for p in ("A4","A9","B4","B9")] + [("F1","1"), ("#FLG01","1")])
    labels(s, "+5V", [("F1","2"), ("C1","1"), ("C2","1"), ("D1","5")])
    labels(s, "GND", [("J1",p) for p in ("A1","A12","B1","B12")] + [("R1","2"),("R2","2"),("C1","2"),("C2","2"),("D1","2"),("#PWR01","1")])
    labels(s, "USB_CC1", [("J1","A5"),("R1","1")]); labels(s, "USB_CC2", [("J1","B5"),("R2","1")])
    labels(s, "USB_D+", [("J1",p) for p in ("A6","B6")] + [("D1",p) for p in ("1","6")])
    labels(s, "USB_D-", [("J1",p) for p in ("A7","B7")] + [("D1",p) for p in ("3","4")])
    labels(s, "USB_SHIELD", [("J1","S1")]); no_connect(s, "J1", ["A8","B8"])


def add_power_block(s: ksa.Schematic) -> None:
    # AP63203WU-7 is fixed 3.3 V / 2 A; FB senses VOUT directly.
    add(s, "Regulator_Switching:AP63203WU", "U2", "AP63203WU-7", (165, 65), "Package_TO_SOT_SMD:TSOT-23-6", "Diodes Inc. AP63203WU-7", LCSC="C780769")
    add(s, "Device:L", "L1", "4.7uH", (190, 65), "Inductor_SMD:L_Vishay_IHLP-2020", "TBD", Status="Inductor candidate pending saturation-current verification")
    add(s, "Device:C", "C3", "100nF", (178, 82), "Capacitor_SMD:C_0402_1005Metric", "Murata GRM155R71C104KA88D")
    add(s, "Device:C", "C4", "22uF", (208, 58), "Capacitor_SMD:C_0805_2012Metric", "Murata GRM21BR60J226ME39L")
    add(s, "Device:C", "C5", "22uF", (208, 78), "Capacitor_SMD:C_0805_2012Metric", "Murata GRM21BR60J226ME39L")
    add(s, "power:PWR_FLAG", "#FLG04", "PWR_FLAG", (130, 42))
    labels(s, "+5V", [("U2","3"),("U2","2"),("#FLG04","1")]); labels(s, "GND", [("U2","4"),("C4","2"),("C5","2")])
    labels(s, "SW", [("U2","5"),("C3","2"),("L1","1")]); labels(s, "BST", [("U2","6"),("C3","1")])
    labels(s, "+3V3", [("U2","1"),("L1","2"),("C4","1"),("C5","1")])


def add_esp32_c3_block(s: ksa.Schematic) -> None:
    u1 = add(s, "Espressif:ESP32-C3-MINI-1", "U1", "ESP32-C3-MINI-1-H4X", (172, 155), "Espressif:ESP32-C3-MINI-1", "Espressif ESP32-C3-MINI-1-H4X", LCSC="C41349510")
    # Separate fields above the module body; its native graphical scale is
    # fixed by the official symbol, so the surrounding whitespace is enlarged.
    u1._data.set_property_effects("Reference", {"position": (154.0, 107.0), "visible": True})
    u1._data.set_property_effects("Value", {"position": (154.0, 113.0), "visible": True})
    add(s, "Device:C", "C6", "100nF", (135, 120), "Capacitor_SMD:C_0402_1005Metric", "Murata GRM155R71C104KA88D")
    add(s, "Device:C", "C7", "1uF", (145, 120), "Capacitor_SMD:C_0402_1005Metric", "Murata GRM155R61A105KE15D")
    add(s, "Device:C", "C8", "10uF", (155, 120), "Capacitor_SMD:C_0805_2012Metric", "Murata GRM21BR61A106KE19L")
    add(s, "power:PWR_FLAG", "#FLG03", "PWR_FLAG", (135, 108))
    labels(s, "+3V3", [("U1","3"),("C6","1"),("C7","1"),("C8","1"),("#FLG03","1")])
    labels(s, "GND", [("U1",str(p)) for p in ["1","2","11","14",*range(36,54)]] + [("C6","2"),("C7","2"),("C8","2")])
    labels(s, "EN", [("U1","8")])
    right_side_labels_with_stubs(s, [
        ("31", "UART_TX", -2.54), ("30", "UART_RX", 0.0),
        ("27", "USB_D+", 0.0), ("26", "USB_D-", 2.54),
        ("6", "STATUS_LED", 0.0), ("20", "I2C_SDA", -2.54),
        ("21", "I2C_SCL", 2.54), ("23", "BOOT", 0.0),
    ])
    no_connect(s, "U1", [str(p) for p in [4,7,9,10,15,17,24,25,28,29,32,33,34,35]])
    # GPIO0/1/2/4/5/8/10 remain reserved, not connected to external circuitry.
    no_connect(s, "U1", ["5","12","13","16","18","19","22"])


def add_reset_boot_block(s: ksa.Schematic) -> None:
    add(s, "Device:R", "R4", "10k", (55, 175), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402FR-0710KL")
    add(s, "Device:C", "C9", "1uF", (70, 175), "Capacitor_SMD:C_0402_1005Metric", "Murata GRM155R61A105KE15D")
    add(s, "Switch:SW_Push", "SW1", "RESET", (90, 175), "Button_Switch_SMD:SW_SPST_B3U-1000P", "Omron B3U-1000P")
    add(s, "Device:R", "R5", "10k", (55, 200), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402FR-0710KL")
    add(s, "Switch:SW_Push", "SW2", "BOOT", (90, 200), "Button_Switch_SMD:SW_SPST_B3U-1000P", "Omron B3U-1000P")
    labels(s, "+3V3", [("R4","1"),("R5","1")]); labels(s, "EN", [("R4","2"),("C9","1"),("SW1","1")]); labels(s, "BOOT", [("R5","2"),("SW2","1")]); labels(s, "GND", [("C9","2"),("SW1","2"),("SW2","2")])


def add_i2c_block(s: ksa.Schematic) -> None:
    for ref, net, x in (("R6","I2C_SDA",240),("R7","I2C_SCL",252)):
        add(s, "Device:R", ref, "4.7k DNP", (x, 115), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402FR-074K7L", Status="DNP selectable")
        labels(s, "+3V3", [(ref,"1")]); labels(s, net, [(ref,"2")])


def add_sen66_connector(s: ksa.Schematic) -> None:
    add(s, "Connector_Generic:Conn_01x04", "J3", "JST PH 4P", (270, 160), "Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal", "S4B-PH-K-S(LF)(SN)",
        Manufacturer="JST", LCSC="C157926", Assembly="TH / hand solder or wave solder candidate",
        Status="Selected; manufacturing release blocked", Notes="Side-entry, keyed; carrier J2 matching")
    labels(s, "+3V3", [("J3","1")]); labels(s, "GND", [("J3","2")]); labels(s, "I2C_SDA", [("J3","3")]); labels(s, "I2C_SCL", [("J3","4")])


def add_qwiic_connector(s: ksa.Schematic) -> None:
    add(s, "Connector_Generic:Conn_01x04", "J2", "Qwiic", (270, 135), "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "JST SM04B-SRSS-TB(LF)(SN)", LCSC="C160404")
    # Qwiic pin order: 1=GND, 2=3V3, 3=SDA, 4=SCL.
    labels(s, "GND", [("J2","1")]); labels(s, "+3V3", [("J2","2")]); labels(s, "I2C_SDA", [("J2","3")]); labels(s, "I2C_SCL", [("J2","4")])


def add_uart_testpoints(s: ksa.Schematic) -> None:
    for ref, net, value, pos in (("TP1","UART_RX","RX",(245,195)), ("TP2","UART_TX","TX",(260,195)), ("TP3","GND","GND",(245,210)), ("TP4","+3V3","3V3",(260,210))):
        add(s, "Connector:TestPoint", ref, value, pos, "TestPoint:TestPoint_Pad_D2.0mm", "Keystone 5015")
        labels(s, net, [(ref,"1")])


def add_status_led(s: ksa.Schematic) -> None:
    add(s, "Device:R", "R8", "2.2k DNP", (275, 195), "Resistor_SMD:R_0402_1005Metric", "Yageo RC0402FR-072K2L", Status="DNP")
    add(s, "Device:LED", "D2", "GREEN DNP", (285, 195), "LED_SMD:LED_0603_1608Metric", "Kingbright APT1608LZGCK", Status="DNP")
    labels(s, "STATUS_LED", [("R8","1")]); labels(s, "LED_K", [("R8","2"),("D2","2")]); labels(s, "GND", [("D2","1")])


def add_power_flags(s: ksa.Schematic) -> None:
    # Distribute test points by function rather than as one dense vertical list.
    testpoints = (
        ("TP5", "+5V", "5V", (215, 42)), ("TP6", "+3V3", "3V3", (215, 55)), ("TP7", "GND", "GND", (215, 68)),
        ("TP8", "I2C_SDA", "SDA", (245, 150)), ("TP9", "I2C_SCL", "SCL", (260, 150)),
        ("TP10", "USB_D+", "USB_D+", (115, 125)), ("TP11", "USB_D-", "USB_D-", (115, 138)),
        ("TP12", "EN", "EN", (100, 175)), ("TP13", "BOOT", "BOOT", (100, 200)),
    )
    for ref, net, value, pos in testpoints:
        add(s, "Connector:TestPoint", ref, value, pos, "TestPoint:TestPoint_Pad_D2.0mm", "Keystone 5015")
        labels(s, net, [(ref,"1")])


def save_and_validate(s: ksa.Schematic) -> None:
    s.save_as(GENERATED)
    # Warnings are retained in the report for review; any ERC error prevents
    # promotion to the formal schematic.
    subprocess.run(["kicad-cli","sch","erc",str(GENERATED),"--output",str(ERC),"--severity-all"], check=True)
    report = ERC.read_text(encoding="utf-8")
    errors = re.search(r"Errors\s+([0-9]+)", report)
    if errors is None or int(errors.group(1)) != 0:
        raise RuntimeError("ERC contains one or more errors; formal schematic not updated")
    subprocess.run(["kicad-cli","sch","export","pdf",str(GENERATED),"--output",str(PDF)], check=True)
    shutil.copyfile(GENERATED, FORMAL)


def main() -> None:
    validate_symbols_and_pins()
    s = load_or_create_schematic(); add_title_block(s); add_usb_c_block(s); add_power_block(s)
    add_esp32_c3_block(s); add_reset_boot_block(s); add_i2c_block(s); add_sen66_connector(s)
    add_qwiic_connector(s); add_uart_testpoints(s); add_status_led(s); add_power_flags(s); add_design_notes(s)
    validate_layout(s); save_and_validate(s)
    print(f"Generated and validated {FORMAL}")


if __name__ == "__main__":
    main()
