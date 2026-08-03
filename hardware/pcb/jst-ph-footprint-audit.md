# JST PH Task A footprint audit — 2026-08-03 JST

Scope: JST `S4B-PH-K-S(LF)(SN)` / C157926 and the installed KiCad 9 footprint. This is an audit only; no footprint or design file was changed.

## Sources and inspected files

- JST, [PH CONNECTOR catalogue ePH.pdf](https://www.jst-mfg.com/product/pdf/eng/ePH.pdf), retrieved 2026-08-03 JST. The PCB-layout drawing is explicitly viewed from the connector mounting surface. It specifies 2.00 mm pitch with ±0.05 mm hole-pitch tolerance and a `φ0.7 +0.1/0` hole; it identifies the No. 1 circuit and cautions that hole dimensions vary with PCB material/drilling process.
- `/usr/share/kicad/footprints/Connector_JST.pretty/JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal.kicad_mod`
  - footprint name: `JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`
  - version: `20241229`; generator: `kicad-footprint-generator`
- Footprint 3D reference: `${KICAD9_3DMODEL_DIR}/Connector_JST.3dshapes/JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal.step`, offset `(0,0,0)`, scale `(1,1,1)`, rotation `(0,0,0)`.
  The referenced STEP/WRL file is **not installed** below `/usr/share/kicad` (and `KICAD9_3DMODEL_DIR` is unset), so its geometry/orientation could not be inspected.

## Direct KiCad extraction

| Item | Extracted KiCad value |
| --- | --- |
| Pads | 1: `(0,0)`, 2: `(2,0)`, 3: `(4,0)`, 4: `(6,0)` mm |
| Pad 1 | through-hole `roundrect`, 1.20 × 1.75 mm, drill 0.75 mm |
| Pads 2–4 | through-hole oval, 1.20 × 1.75 mm, drill 0.75 mm |
| Fab body outline | X=`−1.95…7.95`, Y=`−1.35…6.25` mm; 9.90 × 7.60 mm |
| Silk extents | X=`−2.06…8.06`, Y=`−1.46…6.36` mm; 10.12 × 7.82 mm |
| Courtyard | rectangle X=`−2.45…8.45`, Y=`−1.85…6.75` mm; 10.90 × 8.60 mm |
| 2D height / mating opening | Not encoded as a reliable physical height/orientation in the `.kicad_mod`; requires drawing/3D review. |
| Retention | No extra retention holes/pads; the footprint uses four through-hole contacts only. |

The courtyard is 0.50 mm outside the Fab extents on all four sides. The local Fab drawing includes a triangular No. 1 cue at the pad-1 end; Silk also provides a pad-1-side cue. The footprint's `Horizontal` designation is consistent with the selected side-entry form, but the local coordinate sign of the mating opening is not accepted without the unavailable 3D model or a controlled drawing-view check.

## Official-to-KiCad comparison

| Item | JST official value | KiCad value | Difference | Decision | Note |
| --- | --- | --- | --- | --- | --- |
| Circuits | S4B = 4 circuits | 4 pads | none | PASS | Correct family/count. |
| Pad numbers | Official drawing identifies No. 1 circuit | pads numbered 1–4 | none | PASS | Pad 1 is uniquely roundrect and on the Fab/Silk No. 1 end. |
| Pitch | 2.00 mm, ±0.05 mm PCB-hole pitch | 2.00 mm between centres | 0 | PASS | 0/2/4/6 mm coordinates. |
| Hole | `φ0.7 +0.1/0` in JST PCB-layout drawing | 0.75 mm drill | +0.05 mm against nominal | PASS | 0.75 mm is within the official 0.70–0.80 mm layout range. Fab finished-hole convention is a manufacturing check, not a schematic-assignment blocker. |
| Pad shape/size | No pad OD/shape specified in accessible official catalogue text | Pad 1 roundrect; 2–4 oval; 1.20 × 1.75 mm | N/A | ACCEPTABLE | No official value contradicts the standard footprint. Minimum annular ring from KiCad geometry is 0.225 mm on the 1.20 mm axis; confirm fabricator rules before release. |
| No. 1 orientation | No. 1 shown in mounting-surface PCB-layout view | Pad 1 at `(0,0)` with distinct shape/Fab cue | consistent | PASS | Final board silk must preserve the cue. |
| Body span | S4B catalogue table gives B=9.9 mm | Fab X span 9.90 mm | 0 | PASS | Same nominal span. |
| Body depth | Catalogue drawing is graphical; no unambiguous text value extracted for the S4B body reference plane | Fab Y span 7.60 mm | N/A | VERIFY | Do not infer a physical mating clearance from Fab alone. |
| Body height | Not numerically extracted from controlled catalogue view in this audit | No 2D height | N/A | VERIFY | Requires 3D model or controlled dimensional drawing view. |
| Side-entry / opening | Side-entry header (`S`) | `Horizontal` footprint | family consistent | VERIFY | Opening direction relative to KiCad +/−Y cannot be proven without 3D/drawing-view evidence. |
| Kink/retention | `K` model designation is clinched/kinked in catalogue allocation | No extra retention holes | N/A | VERIFY | Confirm that no separate locating hole is required by the exact part drawing. |
| Fab/Silk/Courtyard | No KiCad-layer geometry in official document | Fab/Silk/Courtyard extents listed above | N/A | ACCEPTABLE | Geometry is internally consistent; verify body/mating keepout in Phase 3B. |
| 3D model availability | N/A | Referenced STEP missing locally; zero transform specified | unavailable | VERIFY | This is an environment/package gap, not evidence of a footprint or part failure. Verify origin, height, Pin-1 end and opening direction in Phase 3B. |

## Pad-number and electrical audit

The current main and carrier symbols use generic 1–4 connector pins, with the fixed mapping `1=3V3`, `2=GND`, `3=SDA`, `4=SCL`. The audited footprint has the same ordered pad set. A footprint rotated by 0°, 90°, 180° or 270° retains pad numbers; only physical location changes. Therefore both J3 and J2 can use this exact footprint while retaining a 1:1 harness: 1→1, 2→2, 3→3, 4→4. A 1↔4 reversed harness remains prohibited.

## Drill, soldering and board-edge rules

The 0.75 mm KiCad drill is compatible with the JST `φ0.7 +0.1/0` layout guidance as a nominal-layout comparison, but the official source does not define the PCB fabricator's pre-plating drill. Confirm the selected fabricator's finished-hole convention before release. The standard footprint's 1.20 × 1.75 mm pads provide a calculated minimum 0.225 mm copper annular ring; this is suitable for review but not a substitute for the fabricator rule check.

For Phase 3B, place the **Fab body**, not the courtyard, within the board outline. The 0.50 mm courtyard does not reserve mating-housing insertion or cable-bend space; add a separate mechanical keepout from the mating face based on the housing and harness bend radius. Keep that corridor clear of the ESP32 antenna at J3 and of SEN66 airflow/strain relief at J2. This is a placement rule only; no coordinates or rotations are decided here.

## Responsibility-separated verdict

| Status | Decision |
| --- | --- |
| Pad numbering, pitch and drill geometry | ACCEPT for schematic/footprint assignment |
| 2D pad/body mapping | ACCEPT — No local-footprint discrepancy was found. |
| Standard KiCad footprint | **ACCEPT:** `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal` |
| Pad OD / annular ring | Manufacturing VERIFY — official catalogue has no pad-OD requirement; selected fabricator rules apply. |
| 3D model availability | Phase 3B mechanical VERIFY — unavailable in this installed environment. |
| Schematic consolidation | **GO:** update main J3 and carrier J2 together, preserve mapping and regenerate/ERC both schematics. |
| Manufacturing release | **BLOCKED:** procurement, fabrication, PCBA and layout gates remain. |

No local footprint creation is authorized or justified by this audit. The missing 3D asset is an environment/package gap, not proof that the 2D footprint is wrong. STEP/WRL installation, model origin/height/opening direction, mating insertion space, cable bend clearance, board-edge placement, airflow, antenna clearance and strain relief are Phase 3B mechanical checks. JLC drill/finished-hole treatment, annular-ring rule, wave-solder fixture, order-time PCBA eligibility, stock/price and CPL rotation are manufacturing checks.
