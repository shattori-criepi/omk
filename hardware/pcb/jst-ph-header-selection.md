# JST PH board-header selection — 2026-08-03 JST

Scope: the board headers for main J3 and carrier J2. This selection changes no schematic, PCB, generator, footprint or BOM/CPL.

## Decision

**Select `S4B-PH-K-S(LF)(SN)` for both main J3 and carrier J2.** It is JST PH, four circuits, 2.00 mm pitch, side-entry/right-angle, through-hole, with a shrouded and polarized mating interface. The base JST model is `S4B-PH-K-S`; JST's official PH catalogue says that this product is marked `(LF)(SN)`, and LCSC/JLC list the purchasable full MPN as `S4B-PH-K-S(LF)(SN)` / `C157926`.

The selected mating harness remains PHR-4 housings ×2, SPH-002T-P0.5S contacts ×8, four AWG26–28 stranded wires, 40 mm nominal finished length, female housing at both ends. The existing electrical mapping is unchanged: 1=3V3, 2=GND, 3=SDA, 4=SCL.

Side-entry TH is preferred over vertical TH because the boards are coplanar and side-by-side: it routes the harness laterally between their facing edges, keeps the harness away from the ESP32 antenna side and avoids a vertical loop in the SEN66 airflow envelope. Through-hole posts and the clinched/kinked retention construction are also more forgiving for Rev.A hand assembly than the selected SMT comparison candidate.

## Official-source record

| Source | Confirmed information |
| --- | --- |
| JST, [PH connector catalogue (ePH.pdf)](https://www.jst-mfg.com/product/pdf/eng/ePH.pdf), retrieved 2026-08-03 JST | PH is 2.00 mm; S4B-PH-K-S is the four-circuit side-entry TH header; 2 A AC/DC (AWG24), 100 V AC/DC, −40 to +105 °C, AWG32–24, PCB thickness 0.8–1.6 mm. It identifies `S` as side-entry, shows a No. 1 circuit mark, and states the `(LF)(SN)` label notation. The illustrated TH layout uses 2.00 mm pitch and `φ0.7 +0.1/0` hole guidance; JST notes the hole size depends on board material/process. |
| [JST PH product page](https://www.jst-mfg.com/product/index.php?lang=2&series=199), retrieved 2026-08-03 JST | Lists B4B-PH-K-S, S4B-PH-K-S and PH SMT header families. |
| [LCSC C157926](https://www.lcsc.com/product-detail/Wire-To-Board-Wire-To-Wire-Connector_JST-Sales-America_S4B-PH-K-S-LF-SN_JST-Sales-America-S4B-PH-K-S-LF-SN_C157926.html), checked 2026-08-03 JST | MPN, 4-position right-angle TH form, and live-stock snapshot. Live data is non-binding. |
| [JLCPCB C157926](https://jlcpcb.com/partdetail/JST_SalesAmerica-S4B_PH_K_S_LF_SN/C157926), checked 2026-08-03 JST | Extended part; wave-solder assembly, Economic/Standard PCBA listing; JLC says a PCB-assembly fixture is required. Live listing, price and eligibility must be rechecked at order. |

JLC's listing describes the right-angle body as 9.9 mm long, 7.6 mm deep and 4.8 mm above board. Use the JST drawing as the controlled source for the final footprint audit rather than relying only on the marketplace rendering.

## Candidate comparison

| Candidate | Form | Result | Reason |
| --- | --- | --- | --- |
| `B4B-PH-K-S(LF)(SN)` / C131334 | Top-entry TH | Not selected | Electrically valid and hand-solder-friendly, but a coplanar 40 mm harness must rise vertically, bend, then descend. This increases height, can obstruct SEN66 airflow and can route near the antenna. |
| **`S4B-PH-K-S(LF)(SN)` / C157926** | **Side-entry TH** | **Selected** | Natural lateral exit, keyed/polarized PH interface, 2 A rating versus 0.35 A SEN66 peak, robust TH retention and direct KiCad 9 footprint. |
| `S4B-PH-SM4-TB` | Side-entry SMT | Alternative only | Official PH catalogue and KiCad library confirm the model/footprint, but it needs SMT-land/paste/CPL control and has lower hand-rework/land-peel margin under cable insertion than the TH part. Consider only when PCBA priority or board area outweighs Rev.A hand-build robustness. |
| `SM04B-PASS-TBT` | Not PH | Rejected | It is a different JST series; do not substitute it into the PH PHR-4/SPH-002T-P0.5S harness system. |

## Footprint and assembly plan

The installed KiCad 9 library contains the exact candidate footprint:

```text
Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal
```

It has pads 1–4 at 2.00 mm pitch, through-hole attributes, silkscreen No. 1 indication, courtyard/fab layers and a STEP-model reference. This is a **footprint candidate, not yet ACCEPT**: before schematic update, compare pad position/diameter, outline, pin-1 mark, board-edge clearance, solder access and 3D body against the controlled JST drawing. The current generic 2.54 mm footprints on J3/J2 are CHANGE.

Place each header on the board edge facing the inter-board gap. Reserve a cable-exit corridor at least the connector body/depth plus the minimum bend envelope; final clearance is a Phase 3B mechanical check. Keep the J3 corridor away from the ESP32 antenna keepout and keep the J2 corridor, strain relief and harness out of the SEN66 intake/exhaust region. Add board silk: Pin-1 triangle, `1 3V3`, `2 GND`, `3 SDA`, `4 SCL`, connector outline and a cable-exit arrow.

## Pin-orientation control and schematic-consolidation gate

The selected electrical harness is **1:1**: pin 1→pin 1, pin 2→pin 2, pin 3→pin 3 and pin 4→pin 4. A 1↔4 reversal is prohibited. PCB rotation never changes a footprint's pad numbers or the schematic net mapping. Therefore the physical board coordinates and rotations are **not** a prerequisite for schematic consolidation.

The following are Phase 3B placement/mechanical checks: opposing-edge opening direction, any bundle-level bend or gentle turn, bend radius, airflow, antenna clearance, strain relief, silk reading direction and CPL rotation. A bundle may turn as a whole; it does not require changing individual conductor order.

Task A was executed on 2026-08-03. Pad numbering, 2.00 mm pitch, 0.75 mm drill geometry, Pad-1 identification and 2D body mapping pass; the standard KiCad footprint is accepted. **Schematic consolidation is GO.** See [jst-ph-footprint-audit.md](jst-ph-footprint-audit.md). The missing STEP model is an environment-package gap and is a Phase 3B mechanical VERIFY, not a schematic blocker. Manufacturing release remains BLOCKED pending layout, procurement and prototype evidence.

## Next work

### Task A — footprint audit

Completed; see [jst-ph-footprint-audit.md](jst-ph-footprint-audit.md). The 2D footprint is accepted; STEP/drawing-view and finished-hole convention are later mechanical/manufacturing checks.

### Task B — schematic consolidation

Task B may start: update main J3 and carrier J2 together, update both generators and properties, preserve the fixed net mapping, regenerate both schematics, run ERC, export PDF/SVG and complete GUI review. Use MPN `S4B-PH-K-S(LF)(SN)`, LCSC `C157926` and `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`. The separate L1 footprint issue may proceed in its own change set; it is not a dependency of this JST PH schematic update.

## Status and remaining blockers

| Item | Electrical | MPN | 2D Footprint | 3D/Mechanical | Procurement | Schematic consolidation | PCB placement/orientation | Manufacturing release |
| --- | --- | --- | --- | --- | --- | --- | --- |
| main J3 / carrier J2, S4B-PH-K-S(LF)(SN) | ACCEPT | ACCEPT | ACCEPT | VERIFY | VERIFY | GO | Phase 3B | BLOCKED |

Remaining evidence for release: Phase 3B STEP/drawing-view, body/opening, mating-space, cable bend/airflow/antenna/strain-relief checks; JLC finished-hole/annular-ring, order-time stock, price, Extended classification, fixture/PCBA confirmation and CPL rotation.
