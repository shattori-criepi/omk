# Phase 3A interconnect review — 2026-08-03 JST

Scope: main J3 to carrier J2, 30–50 mm four-wire harness. No schematic change.

## Decision

**Rev.A recommendation: JST PH 2.0 mm, four positions.** Use B4B-PH-K-S (top-entry TH candidate) or a formally selected side-entry PH header, PHR-4 housing, and SPH-002T-P0.5S contacts for AWG30–24. Use 26–28 AWG stranded wire, 40 mm nominal finished length (30–50 mm acceptable), keyed housing, Pin-1 triangle and `1 3V3 / 2 GND / 3 SDA / 4 SCL` silkscreen on both boards. The harness is **PHR-4 ×2, SPH-002T-P0.5S ×8 and four wires**, with female housings at both ends. JST lists PH as 2.0 mm pitch, 2 A, 100 V, −40 to +105°C, with top/side entry, TH/SMT variations: https://www.jst-mfg.com/product/index.php?lang=2&series=199 .

SEN66 peak supply current is 350 mA; PH's 2 A rating provides electrical margin. At 100 kHz and 30–50 mm, I2C is suitable provided GND is continuous and the existing 4.7 kΩ DNP pull-ups are populated only as needed. Current ordering has GND adjacent to 3V3 but not each signal. A signal-integrity-preferred future order is `GND, SDA, SCL, 3V3`; it changes both schematics and is only a Phase 3B proposal. Keep the existing sequence for Rev.A to avoid unapproved schematic change.

## Comparison

| System | Pitch / current | Lock / key | Hand build | Decision |
| --- | --- | --- | --- | --- |
| Bare 2.54 mm 1×4 | 2.54 mm / ample | none | easiest | REJECT for Rev.A; reverse insertion BLOCKER |
| JST PH | 2.0 mm / 2 A | friction lock / keyed | moderate, widely practical | RECOMMENDED |
| JST GH | 1.25 mm / verify official current | lock / keyed | fine-pitch crimp, harder | Alternative where compactness dominates; BM04B-GHS-TBT, GHR-04V-S, SSHL-002T-P0.2; verify JLC listing |
| JST SH | 1.0 mm / 1 A AWG28 | friction lock / keyed | difficult; Qwiic mix-up risk | Not preferred; SM04B-SRSS-TB, SHR-04V-S. JST source: https://www.jst.com/products/crimp-style-connectors-wire-to-board-type/sh-connector |

JST PH and GH official product data identifies the cited housing/contact families; current LCSC/JLC number, Basic/Extended class, live stock, exact header orientation and PCBA eligibility are **VERIFY** as of this review. KiCad standard footprints exist for common PH/GH/SH headers, but the selected manufacturer drawing, pad numbering, Pin 1, courtyard, 3D, insertion direction, CPL rotation and hand-solder access must be audited before release.

## Required changes before manufacturing release

Electrical: ACCEPT. Mechanical/assembly: CHANGE from bare 2.54 mm. Procurement: VERIFY. Footprint: VERIFY. Manufacturing release: BLOCKED until main J3 and carrier J2 are changed together, official footprint audit is complete, and JLC eligibility/live stock are recorded. Install keyed board headers on both boards and use the female-housing-at-both-ends harness above. B4B-PH-K-S is top-entry TH and is not yet proven optimal for two side-by-side coplanar boards; Phase 3B must compare top-entry TH, side-entry TH and side-entry SMT by bend radius, SEN66 airflow, board spacing, hand-soldering and JLCPCBA compatibility.
