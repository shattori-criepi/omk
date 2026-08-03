#!/usr/bin/env python3
"""Generate the USB-C input proof of concept from the KiCad 9 blank template.

This script deliberately uses kicad-sch-api's parser/editor rather than editing
KiCad S-expressions.  It reads esp32-c3-main.kicad_sch and writes a separate
generated schematic so the blank template remains recoverable.
"""

from __future__ import annotations

import os
from pathlib import Path

import kicad_sch_api as ksa
from kicad_sch_api.library.cache import SymbolLibraryCache, set_symbol_cache


PROJECT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_DIR / "esp32-c3-main.blank.kicad_sch"
OUTPUT = PROJECT_DIR / "esp32-c3-main.generated.kicad_sch"
SYMBOL_DIR = Path(os.environ["KICAD_SYMBOL_DIR"])
CACHE_DIR = PROJECT_DIR / ".cache" / "kicad-sch-api"


EXPECTED_PINS = {
    "Connector:USB_C_Receptacle_USB2.0_16P": {
        "S1", "A1", "A12", "B1", "B12", "A4", "A9", "B4", "B9",
        "A5", "B5", "A7", "B7", "A6", "B6", "A8", "B8",
    },
    "Device:R": {"1", "2"},
    "Device:C": {"1", "2"},
    "Device:Polyfuse": {"1", "2"},
    "Power_Protection:USBLC6-2SC6": {"1", "2", "3", "4", "5", "6"},
    "power:GND": {"1"},
    "power:PWR_FLAG": {"1"},
}


def prepare_symbol_cache() -> None:
    """Use only the explicitly supplied KiCad 9 standard symbol directory."""
    if not SYMBOL_DIR.is_dir():
        raise FileNotFoundError(f"KICAD_SYMBOL_DIR is not a directory: {SYMBOL_DIR}")
    cache = SymbolLibraryCache(cache_dir=CACHE_DIR, enable_persistence=True)
    cache.discover_libraries([SYMBOL_DIR])
    set_symbol_cache(cache)

    for lib_id, expected in EXPECTED_PINS.items():
        symbol = ksa.get_symbol_info(lib_id)
        if symbol is None:
            raise RuntimeError(f"KiCad standard symbol was not found: {lib_id}")
        actual = {pin["number"] for pin in symbol.list_pins()}
        if actual != expected:
            raise RuntimeError(
                f"Unexpected pin set for {lib_id}: expected={sorted(expected)}, "
                f"actual={sorted(actual)}"
            )


def add_labels(schematic: ksa.Schematic, label: str, pins: list[tuple[str, str]]) -> None:
    for reference, pin_number in pins:
        schematic.add_label(label, pin=(reference, pin_number))


def main() -> None:
    prepare_symbol_cache()
    schematic = ksa.load_schematic(TEMPLATE)
    if len(schematic.components) != 0:
        raise RuntimeError(f"Template must be blank; found {len(schematic.components)} component(s)")

    schematic.set_title_block(title="OMK ESP32-C3 MAIN", rev="Rev.A")

    # All component rotations are intentionally 0°: kicad-sch-api 0.5.6 has a
    # known pin-position issue at 90°/270°.
    schematic.components.add(
        "Connector:USB_C_Receptacle_USB2.0_16P",
        reference="J1",
        value="USB-C Receptacle USB2.0 16P",
        position=(50.8, 76.2),
        rotation=0,
        footprint="Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500",
        **{"Manufacturer Part Number": "JAE DX07S016JA1R1500"},
    )
    schematic.components.add(
        "Device:Polyfuse",
        reference="F1",
        value="PTC 1.10A hold",
        position=(88.9, 57.15),
        rotation=0,
        footprint="Fuse:Fuse_1812_4532Metric",
        **{"Manufacturer Part Number": "Bourns MF-MSMF110-2", "Status": "Candidate"},
    )
    for reference, position in (("R1", (88.9, 88.9)), ("R2", (96.52, 88.9))):
        schematic.components.add(
            "Device:R",
            reference=reference,
            value="5.1k 1%",
            position=position,
            rotation=0,
            footprint="Resistor_SMD:R_0402_1005Metric",
            **{"Manufacturer Part Number": "Yageo RC0402FR-075K1L"},
        )
    schematic.components.add(
        "Device:R",
        reference="R3",
        value="0R DNP (shield option)",
        position=(73.66, 119.38),
        rotation=0,
        footprint="Resistor_SMD:R_0402_1005Metric",
        **{"Manufacturer Part Number": "Yageo RC0402JR-070RL", "Status": "DNP; shield-ground policy pending"},
    )
    schematic.components.add(
        "Power_Protection:USBLC6-2SC6",
        reference="D1",
        value="USBLC6-2SC6",
        position=(101.6, 119.38),
        rotation=0,
        footprint="Package_TO_SOT_SMD:SOT-23-6",
        **{"Manufacturer Part Number": "STMicroelectronics USBLC6-2SC6"},
    )
    for reference, value, mpn, footprint, position in (
        ("C1", "10uF 10V X5R", "Murata GRM21BR61A106KE19L", "Capacitor_SMD:C_0805_2012Metric", (127.0, 57.15)),
        ("C2", "100nF 25V X7R", "Murata GRM155R71E104KE14D", "Capacitor_SMD:C_0402_1005Metric", (134.62, 57.15)),
    ):
        schematic.components.add(
            "Device:C",
            reference=reference,
            value=value,
            position=position,
            rotation=0,
            footprint=footprint,
            **{"Manufacturer Part Number": mpn},
        )
    schematic.components.add(
        "power:PWR_FLAG", reference="#FLG01", value="PWR_FLAG", position=(73.66, 57.15), rotation=0
    )
    schematic.components.add(
        "power:GND", reference="#PWR01", value="GND", position=(73.66, 104.14), rotation=0
    )
    schematic.components.add(
        "power:PWR_FLAG", reference="#FLG02", value="PWR_FLAG", position=(66.04, 104.14), rotation=0
    )

    # Direct labels expose the USB nets to the rest of the design.  PWR_FLAG,
    # the GND symbol, and shield option use explicit wires because KiCad ERC
    # requires a physical connection to those power-symbol pins.
    schematic.add_wire_between_pins("J1", "A4", "F1", "1")
    schematic.add_wire_between_pins("F1", "1", "#FLG01", "1")
    schematic.add_wire_between_pins("J1", "A1", "#PWR01", "1")
    schematic.add_wire_between_pins("#PWR01", "1", "#FLG02", "1")
    schematic.add_wire_between_pins("J1", "S1", "R3", "1")
    schematic.add_wire_between_pins("R3", "2", "#PWR01", "1")
    add_labels(schematic, "+5V_USB", [("J1", pin) for pin in ("A4", "A9", "B4", "B9")])
    add_labels(schematic, "+5V_USB", [("F1", "1"), ("#FLG01", "1")])
    add_labels(schematic, "+5V", [("F1", "2"), ("C1", "1"), ("C2", "1"), ("D1", "5")])
    add_labels(schematic, "GND", [("J1", pin) for pin in ("A1", "A12", "B1", "B12")])
    add_labels(schematic, "GND", [("R1", "2"), ("R2", "2"), ("C1", "2"), ("C2", "2"), ("D1", "2"), ("#PWR01", "1")])
    add_labels(schematic, "USB_CC1", [("J1", "A5"), ("R1", "1")])
    add_labels(schematic, "USB_CC2", [("J1", "B5"), ("R2", "1")])
    add_labels(schematic, "USB_D+", [("J1", pin) for pin in ("A6", "B6")])
    add_labels(schematic, "USB_D+", [("D1", pin) for pin in ("1", "6")])
    add_labels(schematic, "USB_D-", [("J1", pin) for pin in ("A7", "B7")])
    add_labels(schematic, "USB_D-", [("D1", pin) for pin in ("3", "4")])
    add_labels(schematic, "USB_SHIELD", [("J1", "S1")])
    for pin_number in ("A8", "B8"):
        pin_position = schematic.get_component_pin_position("J1", pin_number)
        if pin_position is None:
            raise RuntimeError(f"Could not locate J1 pin {pin_number} for No Connect")
        schematic.no_connects.add(pin_position)

    schematic.save_as(OUTPUT)
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    main()
