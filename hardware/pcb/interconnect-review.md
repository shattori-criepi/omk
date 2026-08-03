# Phase 3A interconnect review — 2026-08-03 JST

Scope: main J3 to carrier J2, 30–50 mm four-wire harness. No schematic change.

## Decision

**Rev.A selected header: JST `S4B-PH-K-S(LF)(SN)` / LCSC and JLCPCB `C157926`, for both J3 and J2.** It is the side-entry/right-angle through-hole, four-position PH header. Use PHR-4 housing and SPH-002T-P0.5S contacts for AWG30–24. Use 26–28 AWG stranded wire, 40 mm nominal finished length (30–50 mm acceptable), keyed housing, Pin-1 triangle and `1 3V3 / 2 GND / 3 SDA / 4 SCL` silkscreen on both boards. The harness is **PHR-4 ×2, SPH-002T-P0.5S ×8 and four wires**, with female housings at both ends. Exact source/footprint/orientation evidence is in [jst-ph-header-selection.md](jst-ph-header-selection.md).

SEN66 peak supply current is 350 mA; PH's 2 A rating provides electrical margin. At 100 kHz and 30–50 mm, I2C is suitable provided GND is continuous and the existing 4.7 kΩ DNP pull-ups are populated only as needed. Current ordering has GND adjacent to 3V3 but not each signal. A signal-integrity-preferred future order is `GND, SDA, SCL, 3V3`; it changes both schematics and is only a Phase 3B proposal. Keep the existing sequence for Rev.A to avoid unapproved schematic change.

## Comparison

| System | Pitch / current | Lock / key | Hand build | Decision |
| --- | --- | --- | --- | --- |
| Bare 2.54 mm 1×4 | 2.54 mm / ample | none | easiest | REJECT for Rev.A; reverse insertion BLOCKER |
| JST PH `S4B-PH-K-S(LF)(SN)` | 2.0 mm / 2 A | friction lock / keyed | moderate, widely practical | SELECTED: side-entry TH, C157926 |
| JST GH | 1.25 mm / verify official current | lock / keyed | fine-pitch crimp, harder | Alternative where compactness dominates; BM04B-GHS-TBT, GHR-04V-S, SSHL-002T-P0.2; verify JLC listing |
| JST SH | 1.0 mm / 1 A AWG28 | friction lock / keyed | difficult; Qwiic mix-up risk | Not preferred; SM04B-SRSS-TB, SHR-04V-S. JST source: https://www.jst.com/products/crimp-style-connectors-wire-to-board-type/sh-connector |

JST PH and GH official product data identifies the cited housing/contact families. For the selected S4B-PH-K-S(LF)(SN), LCSC/JLC `C157926` is currently listed as Extended with wave-solder PCBA support requiring a fixture; live stock, price and order-time eligibility remain **VERIFY**. KiCad supplies the exact PH footprint candidate, but JST drawing, pad numbering, Pin 1, courtyard, 3D, insertion direction, CPL rotation and hand-solder access must be audited before release.

## Required changes before manufacturing release

Electrical: ACCEPT. MPN: ACCEPT. Procurement: VERIFY. Footprint: VERIFY pending audit. PCB placement/orientation: Phase 3B. Schematic consolidation: CONDITIONAL GO after the official footprint audit, with main J3 and carrier J2 updated together and the existing mapping retained. Manufacturing release: BLOCKED until broader JLC, layout and prototype gates are complete. Install the selected keyed side-entry TH headers on both boards and use the female-housing-at-both-ends harness above. Physical placement/rotation remains a Phase 3B gate: verify opposing openings, bundle bend/routing, SEN66 airflow, board spacing, hand-soldering and JLCPCBA fixture compatibility.
