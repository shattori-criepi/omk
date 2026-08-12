# J1 USB-C footprint監査 — DX07S016JA1R1500

Audit date: 2026-08-03 15:32 JST.  This is a document-only Phase 3A/3B gate review.  It does **not** alter the schematic, generator, footprint file, 3D model, PCB, PDF/SVG, BOM/CPL, or manufacturing data.

## 対象範囲と管理された出典

| Source | Controlled use | Revision / availability at audit |
| --- | --- | --- |
| [JAE product page — DX07S016JA1R1500](https://products.jae.com/gl/en/connectors/category/io/dx07-receptacle/dx07s016ja1r1500/) | Identity, status and public document identifiers | Product is **Active**; 16-position, one-row SMT receptacle, USB Type-C; drawing `SJ121837`, specification `JACS-30413`, handling instructions `JAHL-30353-1`; retrieved 2026-08-03 JST. |
| JAE drawing `SJ121837` and specification `JACS-30413` | Controlled source for exact mechanical dimensions, recommended land, views, tolerances, reflow and packaging | Not publicly downloadable in this environment: the product page exposes its 2D/3D/material-data routes through login / technical-document request. Revision/date and their contents are therefore **not verified**. |
| JAE handling instructions `JAHL-30353-1` | Controlled source for mating, handling, reflow, tape and MSL conditions | Not obtained for the same access reason; revision/date and contents are **not verified**. |
| [LCSC C3197885](https://lcsc.com/product-detail/USB-Connectors_JAE-Electronics-DX07S016JA1R1500_C3197885.html) | Distributor identity / order-time availability only | Public listing matched JAE `DX07S016JA1R1500`, LCSC `C3197885` and SMD. Its crawl showed 355 pieces, but this is not live/order-flow evidence. |
| [JLCPCB C3197885](https://jlcpcb.com/partdetail/JAEElectronics-DX07S016JA1R1500/C3197885) | Official assembler listing / assembly class | Public page retrieved 2026-08-03 JST identifies the same MPN as **Extended**, SMD, SMT Assembly, Economic and Standard PCBA, Assembly Difficulty **High**, MSL 1. Search-cache stock/price shown on that page are not treated as order-time evidence; exact availability, fixture/CPL rotation and acceptance remain **VERIFY**. |

No third-party drawing or image was used to fill missing dimensions.  The official drawing must be acquired from JAE before manufacturing release.

## KiCad footprintの直接抽出

The exact installed file is:

```
/usr/share/kicad/footprints/Connector_USB.pretty/USB_C_Receptacle_JAE_DX07S016JA1R1500.kicad_mod
```

| Field | Extracted value |
| --- | --- |
| Footprint / library name | `Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500` |
| File format metadata | `version 20241229`; `generator "pcbnew"`; `generator_version "9.0"` |
| Attribute | `smd` (the footprint nevertheless contains plated through-hole shell pads) |
| Edge.Cuts / keepout | None in the `.kicad_mod`; the board edge and mating keepout must be created in the PCB, not inherited from this footprint. |
| Fab outline | X = −4.47…+4.47 mm, Y = −3.30…+3.60 mm: 8.94 × 6.90 mm. |
| Silk outline | Side/rear outline at X=±4.58 mm and Y=3.71 mm; deliberately open at the mating side. |
| Courtyard | X = −5.47…+5.47 mm, Y = −4.33…+4.10 mm: 10.94 × 8.43 mm. It is an assembly clearance outline, not the mating-plug/cable envelope. |
| 3D reference | `${KICAD9_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C_Receptacle_JAE_DX07S016JA1R1500.step`; offset `(0,0,0)`, scale `(1,1,1)`, rotation `(0,0,0)`. |
| Installed 3D evidence | No matching STEP or WRL was present under `/usr/share/kicad` in this environment. This is package/content absence, not evidence that the footprint or physical part is wrong. |

All signal lands are F.Cu/F.Mask/F.Paste.  Thus the footprint requests normal copper, solder-mask opening and stencil paste for the signal lands.  The four shell pads are `thru_hole`, on `*.Cu`/`*.Mask`, with `pad_prop_heatsink`; they carry no paste layer.  The two unnumbered rectangular SMD mechanical pads use F.Cu/F.Mask/F.Paste.  The two unnumbered locating holes are NPTH and have no copper/paste connection.

### pad一覧（KiCad座標系、mm）

| Classification | Pads / coordinates and size |
| --- | --- |
| A-row signal | A1 (−3.10, −3.05), A4 (−2.35, −3.05), A5 (−1.75, −3.05), A6 (−0.25, −3.05), A7 (+0.75, −3.05), A8 (+1.75, −3.05), A9 (+2.35, −3.05), A12 (+3.10, −3.05). A1/A4/A9/A12 are 0.52 × 1.00 mm; A5/A6/A7/A8 are 0.27 × 1.00 mm. |
| B-row signal | B1 (+3.10, −3.05), B4 (+2.35, −3.05), B5 (+1.25, −3.05), B6 (+0.25, −3.05), B7 (−0.75, −3.05), B8 (−1.25, −3.05), B9 (−2.35, −3.05), B12 (−3.10, −3.05). B1/B4/B9/B12 are 0.52 × 1.00 mm; B5/B6/B7/B8 are 0.27 × 1.00 mm. |
| Intentional duplicate land pairs | A1/B1, A4/B9, A9/B4 and A12/B12 share identical physical lands. This models the reversible USB-C plug contacts and is normal KiCad multi-pad-to-one-land usage. |
| S1 shell / shield | Four identical-number plated oval PTH pads: (−4.32,−2.675), (−4.32,+1.15), (+4.32,−2.675), (+4.32,+1.15). Upper/rear pair pad sizes are 1.30 × 2.30 mm with 0.60 × 1.60 mm oval drill; lower/front pair 1.30 × 2.60 mm with 0.60 × 1.90 mm oval drill. |
| Mechanical / locating | Unnumbered NPTH circle (−3.00,−1.95), drill 0.60 mm; unnumbered NPTH oval (+3.00,−1.95), drill/size 0.85 × 0.60 mm. Unnumbered SMD rectangles at (−1.40,+1.15) and (+1.40,+1.15), 1.00 × 2.00 mm. |

For completeness, A2/A3/A10/A11 and B2/B3/B10/B11 are absent: they are the SuperSpeed contacts omitted by this 16-position USB2-only part.  The 16-pad hardware therefore has only the USB 2.0/power/control contact set; these are not missing KiCad pads.  The footprint has no individually numbered VBUS, GND or shell pad because the USB-C contact designators themselves are the pad numbers.

## 回路図 / pad mapping監査

The formal symbol is `Connector:USB_C_Receptacle_USB2.0_16P`; the generator and formal schematic assign the matching KiCad footprint.  Its electrical mapping is unchanged and every electrical symbol pin named below exists in the footprint.

| Function | Symbol / footprint pads | OMK net / treatment | Result |
| --- | --- | --- | --- |
| VBUS | A4, A9, B4, B9 | `+5V_USB`, then F1 | ACCEPT |
| Signal GND | A1, A12, B1, B12 | `GND` | ACCEPT |
| D+ | A6, B6 | `USB_D+` to D1, then GPIO19 | ACCEPT |
| D− | A7, B7 | `USB_D-` to D1, then GPIO18 | ACCEPT |
| CC1 / CC2 | A5 / B5 | Separate `USB_CC1` / `USB_CC2`, each through its own 5.1 kOhm Rd to GND | ACCEPT |
| SBU1 / SBU2 | A8 / B8 | Explicit No Connect; no PD, alternate mode or SBU use | ACCEPT |
| Shell | S1 ×4, common pad number | `USB_SHIELD`, only connectable to GND through R3 0 Ohm DNP option | ACCEPT |

No SuperSpeed symbol pins are required by this USB2.0_16P symbol or this 16-position part.  The same-number S1 pads are intentionally one shield net, not accidental signal-GND shorts.  R3 remains DNP and no chassis ground exists in Rev.A; this audit does not change that circuit decision.

## 公式値とKiCadの比較

| Item | JAE official value | KiCad value | Difference | Finding / note |
| --- | --- | --- | --- | --- |
| Part identity and contact count | Product page: DX07S016JA1R1500, 16 pos., one-row SMT receptacle | Named MPN-specific 16-contact USB2 footprint | — | PASS identity only. |
| A/B contact numbering and signal positions | Requires drawing `SJ121837` | A/B pad designators listed above | — | VERIFY: official numbering view unavailable. |
| Signal-land X/Y, pitch, row spacing, width/length | Requires `SJ121837` / recommended land | See pad inventory; all signal pad centres Y=−3.05 mm | — | VERIFY; do not infer approval from matching name. |
| Shell stakes, X/Y, land and drill | Requires `SJ121837` / recommended land | S1 at X=±4.32; oval drills 0.60×1.60 and 0.60×1.90 mm | — | VERIFY. |
| Locating NPTH and mechanical SMD lands | Requires `SJ121837` / recommended land | NPTH at (−3.00,−1.95) and (+3.00,−1.95); mechanical SMD lands at X=±1.40, Y=+1.15 | — | VERIFY. |
| Body width/depth/height | Requires drawing / spec | Fab body 8.94 × 6.90 mm; height not represented in 2D | — | VERIFY. |
| Fab/silk/courtyard | JAE does not define KiCad layer graphics | Values in direct extraction | N/A | ACCEPTABLE as KiCad library graphics only; product envelope and assembly clearance still require official drawing. |
| Board-edge datum, front overhang and mating clearance | Requires mounting/mating view | No edge datum, Edge.Cuts or keepout in footprint | — | VERIFY / Phase 3B rule below. |
| Paste / mask | Requires JAE recommended land / assembly data | Signal and SMD mechanical pads use F.Paste/F.Mask; PTH shell pads do not | — | VERIFY for manufacturing release. |
| Pin-1/orientation cue | Requires official mounting-side view | Pad names are visible in CAD; no explicit numeric Pin-1 silk marker | — | VERIFY. |
| 3D origin, rotation, height | Requires JAE 3D/model drawing | Reference is zero offset/rotation; file unavailable locally | — | VERIFY. |
| Board thickness, coplanarity, insertion depth, reflow, tape, MSL, RoHS | Requires `JACS-30413` / `JAHL-30353-1` / certificates | Not encoded in footprint | — | VERIFY. RoHS certificate route exists on product page but was not retrievable anonymously. |

## 基板端、shell、Phase 3B配置規則

The KiCad origin is **not** declared by the footprint as a PCB-edge datum.  Phase 3B shall obtain `SJ121837`, identify its mounting-side board-edge datum and align that datum (not an arbitrary Fab or courtyard line) to the board `Edge.Cuts`.  Do not infer an overhang, cutout or notch requirement from the current coordinates.

Until that drawing is available, these constraints apply:

- Keep all four S1 plated shell stakes, both locating NPTHs and both mechanical SMD lands within the PCB; do not place copper, via-in-pad, slots or board edge through them.
- Treat the footprint courtyard as component-to-component assembly clearance only.  Define a separate mechanical keepout from the official mating plug/cable envelope before setting the enclosure wall, mounting holes or hand-rework access.
- Make the shell return short and low inductance to the selected shield strategy; do not thermal-relief or split it by accident.  Keep the four common S1 pads on `USB_SHIELD`, not electrical `GND`, unless R3 is deliberately populated after evaluation.
- Confirm plated-hole manufacturing capability, annular-ring rules, paste/mask expansion, reflow profile and connector retention with the selected assembler.  The present shell pads are PTH, so ordinary top-side reflow eligibility is not assumed.

### Phase 3BのUSB 2.0 routing規則

| Basis | Rule |
| --- | --- |
| USB / electrical good practice | Put D1 (USBLC6-2SC6) immediately after J1; provide its shortest possible GND return to a continuous reference plane. Route D+/D− as a 90 Ohm differential pair for the selected stack-up, with matched pair lengths, no plane splits, minimal vias and no long stubs. |
| OMK-specific | Place TP10/TP11 as inline pads or exceptionally short stubs; keep CC1/CC2 away from D+/D−, make the VBUS/F1 path wide and short, and keep USB routing away from U2 SW/L1 and the ESP32 antenna keepout. |
| JAE-controlled, pending drawing | Contact-side copper restrictions, exact edge alignment, plug insertion/cable envelope, body height, assembly/rework access and shell-stake land geometry. |
| Espressif-controlled | Preserve the ESP32-C3-MINI antenna keepout and do not route USB/buck return currents through it. |

## 調達と代替候補

LCSC identity `C3197885` was matched to the MPN.  JLCPCB's public C3197885 page classifies it as **Extended**, SMD / SMT Assembly, Economic and Standard PCBA, Assembly Difficulty High and MSL 1.  Its displayed stock/price is cache-visible rather than an order-time commitment.  Live price/MOQ, consignment/pre-order requirement, fixture requirement, PnP/CPL rotation, selective-solder/wave/reflow method and final PCBA eligibility were not established in a logged-in JLC order flow, therefore remain **VERIFY**.

No alternative was selected.  An alternative comparison is deliberately deferred unless this JAE part fails its official drawing or order-time procurement gate: a USB2-only 16-contact receptacle is not a drop-in replacement unless its contact lands, shell stakes, locating holes, mounting style (top/mid mount), board-edge datum and enclosure clearance all match.  Reusing this footprint for another MPN is prohibited without a separate audit.

## 最終決定と次作業

| Responsibility | Status | Reason |
| --- | --- | --- |
| Electrical symbol mapping | ACCEPT | All intended USB2/power/control and shield pins map to real footprint pads; NCs are explicit. |
| Current KiCad footprint identity / 2D pad enumeration | ACCEPTABLE | MPN-specific standard KiCad footprint exists and has coherent 16-contact, duplicate-contact and shell treatment. |
| Official signal / shell / NPTH geometry | VERIFY | `SJ121837` and recommended land are not available in this environment. |
| Board-edge definition and mechanical envelope | VERIFY | No footprint edge datum/keepout; official mounting/mating views required. |
| 3D model | VERIFY | Correct zero-transform model reference, but model file not installed and no JAE model was available. |
| Procurement / assembly | VERIFY | LCSC identity only; no order-time JLCPCBA evidence. |
| Schematic integration | No change | Existing J1 symbol/footprint assignment remains; task explicitly prohibits generator/formal-schematic change and LCSC-property update. |
| Phase 3B placement planning | CONDITIONAL GO | Electrical placement/routing planning may proceed, but freeze of J1 edge placement requires the official drawing. |
| Manufacturing release | BLOCKED | Official geometry/assembly documents, order-time assembler eligibility and 3D/mechanical checks remain open. |

**Conclusion B — standard footprint VERIFY.**  The named KiCad footprint is not rejected, but the controlled JAE dimensional evidence required to mark it ACCEPT has not been obtained.  Next task: request/obtain JAE `SJ121837`, `JACS-30413` and `JAHL-30353-1`, compare every pad/mechanical/edge dimension to this audit, then conduct the logged-in JLC order-flow and 3D/enclosure checks.  A local footprint is not specified or created until that comparison identifies a real mismatch.
