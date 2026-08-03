# L1 Coilcraft XGL4020-472MEC footprint audit — 2026-08-03 JST

Initial scope was documentation and audit only. **Integration update, 2026-08-03 JST:** the generator and formal schematic now use the accepted `Inductor_SMD:L_Coilcraft_XxL4020`, MPN `XGL4020-472MEC` and LCSC `C6012418`; the generator was rerun deterministically, ERC/PDF/SVG passed, and the L1 netlist was unchanged. No PCB, footprint-library file, BOM/CPL, commit or push was changed. The earlier `Inductor_SMD:L_Vishay_IHLP-2020` assignment is retained below solely as rejected-history evidence.

## Controlled sources

All package and land-pattern dimensions below come from Coilcraft primary sources, retrieved 2026-08-03 JST.

- Coilcraft, [*Shielded Power Inductors — XGL4020*, Document 1529-1, revised 2026-02-19](https://www.coilcraft.com/getmedia/76c9c081-4945-4c85-9129-9356e1ad6734/xgl4020.pdf): electrical table, package drawing, recommended land pattern, high-dv/dt terminal direction and temperature conditions.
- Coilcraft, [XGL4020-472MEC product page](https://www.coilcraft.com/en-us/products/power/high-voltage-inductors/xgl/xgl4020/xgl4020-472/): exact MPN, 4.7 uH value, 3.0 A Isat at 20% L drop, 5.6 A Irms at 40 °C rise, RoHS/halogen-free, `E` termination and `C` 7-inch machine reel.
- Coilcraft, [XGL4020 series page](https://www.coilcraft.com/en-us/products/power/shielded-inductors/molded-inductor/xgl/xgl4020/): shielded composite construction, 1000/7-inch or 3500/13-inch reel, 12 mm tape, 8 mm pocket pitch, 2.3 mm pocket depth, operating/storage limits and MSL 1.
- Coilcraft, [XFL4020 product page](https://www.coilcraft.com/en-us/products/power/shielded-inductors/molded-inductor/xfl/xfl4020/): Coilcraft identifies XGL4020 as a drop-in replacement for XFL4020. This supports use of the installed common `XxL4020` 2D land pattern only after the numerical comparison below; it does not substitute for the XGL drawing.
- Coilcraft, [*Soldering Surface Mount Components*, Document 362-1, revised 2018-06-15](https://www.coilcraft.com/getmedia/06a8e448-75f0-480e-a120-e1ea952fecf4/Doc362_SolderingSMT.pdf): process guidance. The XGL4020 data sheet explicitly directs the reader to this document before soldering.

The accessible XGL data sheet gives no separate coplanarity limit, solder-mask expansion, paste-aperture rule, or part-specific reflow profile. Therefore those values are not invented here: use the board assembler's process rules and Coilcraft Document 362, then validate the paste/mask result in the next footprint-integration task. MSL is 1 (unlimited floor life below 30 °C/85% RH), RoHS and halogen-free are stated by Coilcraft. `M` denotes ±20% tolerance, `E` tin-silver (96.5/3.5) over copper, and `C` a 7-inch machine-ready reel. The reel page establishes tape orientation, but this environment cannot make a controlled visual comparison between that drawing and an installed 3D model.

## Official XGL4020-472MEC package and land data

| Item | Coilcraft controlled value | Basis / note |
| --- | --- | --- |
| Body X × Y | 4.00 ±0.30 × 4.00 ±0.30 mm | Document 1529-1 package drawing |
| Body height | 2.10 mm max | Document 1529-1 package drawing; not 2.0 mm nominal |
| Terminal width | 0.82 ±0.05 mm | Package drawing |
| Terminal length | 1.57 ±0.25 mm | Package drawing |
| Terminal-to-terminal gap | Not separately dimensioned | Do not derive a production dimension from drawing elements with different datums. |
| Recommended copper pad, each | 0.98 × 3.40 mm | Document 1529-1 recommended land pattern |
| Pad centre distance | 2.37 mm | Document 1529-1 recommended land pattern |
| Copper gap between pads | 1.39 mm | Calculated only from the controlled land: 2.37 − 0.98. |
| Polarity / start lead | Electrically non-polar two-terminal part; drawing marks the short/start lead | Coilcraft says to connect high `dv/dt` to the indicated short/start lead for lowest EMI. This is an assembly-orientation requirement, not a different net/pin function. |
| Pin numbers | No package pin numbers specified | KiCad pads 1/2 are schematic identities; map SW/output consistently and preserve the physical start-lead orientation during placement. |
| Magnetic construction | Magnetically shielded composite | Series/product pages |

## Current footprint: direct KiCad extraction

Inspected installed file: `/usr/share/kicad/footprints/Inductor_SMD.pretty/L_Vishay_IHLP-2020.kicad_mod`.

| Item | Extracted value |
| --- | --- |
| Footprint / metadata | `L_Vishay_IHLP-2020`; version `20241229`; generator `kicad-footprint-generator` |
| Declared body | Vishay IHLP-2020, 5.18 × 5.18 × 3.0 mm |
| Pads | `1` roundrect at `(-2.4255, 0)`, `2` roundrect at `(2.4255, 0)` mm |
| Pad size / centre distance | 1.905 × 2.79 mm each; 4.851 mm centres; 2.946 mm copper gap |
| Fab | rectangle `X=-2.59…2.59`, `Y=-2.59…2.59` mm (5.18 × 5.18 mm) |
| Silk | extents approximately `X/Y=-2.70…2.70` mm, with pad-side openings |
| Courtyard | `X=-3.63…3.63`, `Y=-2.84…2.84` mm (7.26 × 5.68 mm) |
| 3D reference | `${KICAD9_3DMODEL_DIR}/Inductor_SMD.3dshapes/L_Vishay_IHLP-2020.step`, zero offset/rotation |
| Installed 3D asset | Not present below `/usr/share/kicad`; `KICAD9_3DMODEL_DIR` is unset in this environment. |

## Numerical comparison and standard-library search

The installed `Inductor_SMD.pretty` tree contains no `XGL4020` footprint. The two applicable existing Coilcraft candidates below were read directly; no generic or unverified 4 mm footprint is being assumed.

| Item | Coilcraft XGL4020 official | Current `L_Vishay_IHLP-2020` | `L_Coilcraft_XxL4020` | `L_Coilcraft_XAL4020-XXX` | Result |
| --- | --- | --- | --- | --- | --- |
| Exact installed path | — | `/usr/share/kicad/footprints/Inductor_SMD.pretty/L_Vishay_IHLP-2020.kicad_mod` | `/usr/share/kicad/footprints/Inductor_SMD.pretty/L_Coilcraft_XxL4020.kicad_mod` | `/usr/share/kicad/footprints/Inductor_SMD.pretty/L_Coilcraft_XAL4020-XXX.kicad_mod` | — |
| File metadata | — | `20241229`, `kicad-footprint-generator` | `20241229`, `pcbnew`, generator version `9.0` | `20241229`, `kicad-footprint-generator` | Direct extraction |
| Body X × Y | 4.00 ±0.30 × 4.00 ±0.30 | 5.18 × 5.18 | 4.00 × 4.00 Fab | 4.30 × 4.30 Fab | IHLP FAIL; XxL PASS; XAL REJECT for this MPN |
| Height | 2.10 max | 3.0 declared | no height property | 2.1 declared | IHLP FAIL; model/height evidence remains VERIFY for the candidates |
| Recommended pad | 0.98 × 3.40 | 1.905 × 2.79 | 0.98 × 3.40 | 0.98 × 3.40 | IHLP FAIL; both Coilcraft candidates PASS on copper size |
| Pad centre distance | 2.37 | 4.851 | 2.37 (`x=±1.185`) | 2.37 (`x=±1.185`) | IHLP FAIL; candidates PASS |
| Copper gap | 1.39 | 2.946 | 1.39 | 1.39 | IHLP FAIL; candidates PASS |
| Pad 1 / 2 | no official package numbering | 1 / 2 | 1 / 2; Pad 1 rectangular | 1 / 2; both roundrect | XxL ACCEPT for schematic identity; physical short-lead orientation remains Phase 3B VERIFY |
| Fab | controlled body 4.0 class | 5.18 × 5.18 | `X/Y=-2.00…2.00` | `X/Y=-2.15…2.15` | XxL PASS; XAL fails exact body match |
| Silk | no prescribed KiCad silk | 5.40 × 5.40 outline | top/bottom only at `Y=±2.154` | square outline `X/Y=±2.26` | XxL ACCEPTABLE; XAL not selected |
| Courtyard | no official courtyard rule | 7.26 × 5.68 | 4.52 × 4.52 (`X/Y=±2.26`) | 4.80 × 4.80 (`X/Y=±2.40`) | XxL ACCEPTABLE; add assembly clearance separately |
| 3D reference | Coilcraft page offers a 3D model | Vishay IHLP STEP | `L_Coilcraft_XxL4020.step`, zero transform | `L_Coilcraft_XAL4020-XXX.step`, zero transform | All referenced STEP files unavailable in this installed environment; XGL 3D orientation is VERIFY |

`L_Coilcraft_XxL4020` is a common XFL/XEL/XGL-family 4.0 mm footprint rather than an MPN-named XGL file. Its Fab body and all three controlling land values exactly match Document 1529-1. Coilcraft's stated XGL/XFL drop-in relationship provides additional controlled support. `L_Coilcraft_XAL4020-XXX` has matching pads but a 4.30 mm Fab body and an XAL-specific 3D reference; do **not** use it for XGL4020-472MEC.

## Verdict and implementation gate

| Status | Decision |
| --- | --- |
| Electrical | ACCEPT — unchanged, XGL4020-472MEC is the selected electrical MPN. |
| Historical `L_Vishay_IHLP-2020` | **REJECTED / replaced** — body +1.18 mm in both axes, height +0.90 mm, pad centres +2.481 mm and wrong land geometry. |
| 2D footprint | **ACCEPT / integrated:** `Inductor_SMD:L_Coilcraft_XxL4020` |
| Local footprint | **Not required.** No dimensional mismatch justifies creating `OMK.pretty:Coilcraft_XGL4020` for the 2D land pattern. |
| 3D / mechanical | VERIFY — installed KiCad package lacks the referenced STEP files. Obtain Coilcraft's official XGL4020 model or install/inspect the matching KiCad model before 3D collision sign-off. This is not evidence that the 2D footprint is wrong. |
| Procurement | VERIFY — official pages found at [LCSC C6012418](https://www.lcsc.com/product-detail/C6012418.html) and [JLCPCB C6012418](https://jlcpcb.com/partdetail/Coilcraft-XGL4020472MEC/C6012418), retrieved 2026-08-03 JST. They list SMD 4×4 mm and Extended, but live stock, price, PCBA eligibility, MOQ and order-time availability must be rechecked in the logged-in order flow. |
| Schematic consolidation for L1 | **COMPLETE** — generator/formal assignment, MPN/LCSC/status, ERC/PDF/SVG and netlist-preservation checks completed. GUI review remains a Phase 3B pre-layout check. |
| Phase 3B placement planning | **GO** using the integrated 2D footprint; do not issue fabrication/manufacturing release until MLCC DC-bias evidence and procurement checks are complete. |
| Manufacturing release | BLOCKED — 3D/mechanical, procurement and MLCC release gates remain. |

## Phase 3B placement rules

The first four rules are direct consequences of the AP63203 layout guidance already recorded in `power-review.md`; the remaining rows are OMK placement rules for the stated ESP32/SEN66 design.

| Rule | Source / classification |
| --- | --- |
| Put U2 SW-to-L1 copper as short as possible and keep the SW-node copper area small. | AP63203 layout guidance |
| Put C3 immediately BST–SW; do not route FB beside SW or under the inductor. | AP63203 layout guidance |
| Route L1 output to C4/C5 with short, wide copper and close return paths; keep the input hot loop separated from the output loop. | AP63203 layout guidance |
| Use the continuous GND plane/short return vias prescribed for the buck. | AP63203 layout guidance |
| The Coilcraft drawing identifies one short/start terminal for high `dv/dt`; orient that terminal to the SW side after controlled visual/tape-orientation verification. | Coilcraft requirement; Phase 3B assembly check |
| Do not place a test point directly on SW. If SW probing is needed, provide only a deliberately reviewed, very short probe pad. | OMK EMI/probing rule |
| Treat the 4.52 × 4.52 mm KiCad courtyard as component-to-component clearance only; reserve extra clearance for rework and no copper keepout is implied by it. | OMK mechanical rule |
| Do not assume a copper keepout below the shielded inductor. Decide the local GND plane/thermal-via pattern from the buck return-current design, then verify conducted/radiated EMI on Rev.A. | OMK rule; Coilcraft data sheet does not prescribe a PCB-underbody keepout |
| Keep the buck/L1 magnetic and switching region away from the SEN66 airflow/temperature-sensitive zone, ESP32 antenna keepout, USB D+/D− pair and sensitive I2C routes. | OMK system rule; ESP32/SEN66 design constraints |
| Apply fabricator-standard solder-mask expansion and paste aperture first; no Coilcraft-specific paste reduction is specified. Review stencil result and thermal balance with the selected assembler. | Controlled-source limitation + manufacturing rule |

## Integration result and remaining task

Standard-footprint integration is complete: the generator now holds the selected MPN/LCSC/status and `Inductor_SMD:L_Coilcraft_XxL4020`; L1 pad 1 remains `SW`, pad 2 remains `+3V3`, the complete netlist is unchanged, and the formal schematic was regenerated with 0 errors / the two existing U1 library warnings. PDF and SVG were re-exported. GUI operation is unavailable in this environment, so GUI visual review remains a Phase 3B pre-layout check.

The remaining task is Phase 3B verification: obtain/install the controlled XGL 3D model; verify origin, height, short/start-lead direction, courtyard/rework clearance, pick-and-place rotation and tape orientation. Before order, recheck JLC/LCSC availability/PCBA status and retain the order-time evidence. No BOM/CPL is changed by this integration.

If a future controlled drawing revision invalidates the 0.98 × 3.40 mm / 2.37 mm land, then create a local file only as `hardware/pcb/esp32-c3-main/footprints/OMK.pretty/Coilcraft_XGL4020.kicad_mod`, with pads 1/2 at `(-1.185,0)/(+1.185,0)`, each `0.98 × 3.40 mm`, the approved mask/paste rule, 4.00 × 4.00 mm Fab body, an assembler-approved courtyard, a documented start-lead cue, source URL/revision and DRC/3D checks. That contingency is **not** authorized or needed now.
