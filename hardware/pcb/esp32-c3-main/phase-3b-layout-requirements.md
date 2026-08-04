# Phase 3B PCB layout requirements — historical record

> **Project status: CANCELLED (2026-08-04). Manufacturing release: CANCELLED. Fabrication permitted: NO.**
>
> 本文は中止前に整理した実装要件であり、配線、stack-up選定、製造データ作成または後継設計への自動適用を指示しない。未配線PCB、仮配置座標、未確定stack-upおよびUSB geometryは参考資料としてのみ保持する。詳細は[中止決定記録](../../../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)を参照。

**Prepared:** 2026-08-03 JST

This document consolidates the completed Phase 3A audit records into implementation requirements for the **main board** PCB. It does not create or modify a PCB, schematic, generator, footprint, BOM/CPL, or Gerber file. Requirements are classified as **MUST**, **SHOULD**, **VERIFY**, or **BLOCKED**; a numeric geometry is deliberately not fixed where the stack-up, controlled drawing, assembler rule, or official source is still missing.

## 1. Scope and phase gate

### Phase 3B purpose

Implement and review the main-board placement, routing, copper/keepout rules and mechanical interfaces while preserving the formal-schematic netlist. Carrier-board placement is a separate PCB task; its J2 connector must remain electrically and mechanically compatible with main-board J3.

### Accepted electrical topology

- U1: `ESP32-C3-MINI-1-H4X`; antenna-module 2D footprint, pin mapping and EPAD 49 mapping are accepted.
- USB: J1 → D1 `USBLC6-2SC6` → U1.27 D+ / U1.26 D−. D1 mapping and rail-to-rail topology are accepted.
- Buck: U2 `AP63203WU-7`, C1 10 uF, C2/C3 100 nF, L1 `XGL4020-472MEC` 4.7 uH, C4/C5 22 uF ×2. U2 pin/net topology and L1 2D footprint are accepted.
- Board link: main J3 and carrier J2 are both JST `S4B-PH-K-S(LF)(SN)` / C157926, side-entry TH; mapping is fixed as 1=3V3, 2=GND, 3=I2C_SDA, 4=I2C_SCL.

### Gate status

**Historical Phase 3B gate (superseded):** PCB implementation had been planned conditionally. The integrated-PCB plan is now **CANCELLED**; do not start or resume implementation from this document. The then-open items in Sections 12 and 14 remain incomplete and do not constitute a manufacturing-release gate for any successor design.

## 2. Board-level constraints

| Area | Requirement / current status | Classification | Evidence / source |
| --- | --- | --- | --- |
| Layer stack | Four layers are recommended by Espressif; actual layer count, dielectric thickness, copper weight, impedance geometry and via rules are not selected. | VERIFY | Espressif PCB guidance; selected fabricator stack-up. |
| Board outline | Do not freeze the outline before U1 antenna/end-of-board rule, J1 mating datum and J3 cable corridor are simultaneously reviewed. | MUST | U1, J1 and JST audits. |
| USB-C edge | J1 is edge-facing, but its exact datum/overhang/mating envelope is not fixed because JAE `SJ121837` is unavailable in the present audit record. | VERIFY | `usb-c-j1-footprint-audit.md`. |
| Carrier cable | Reserve an edge-facing, lateral exit corridor for J3 and a 40 mm nominal, 1:1 PH harness. Keep it clear of the antenna and SEN66 airflow route. | MUST | `jst-ph-header-selection.md`, `interconnect-review.md`. |
| Enclosure | Respect the ESP32 antenna clearance inside the enclosure; do not use the component courtyard as an enclosure keepout. | MUST | ESP32-C3 module audit. |
| Assembly / service | Reserve hand-solder/rework access for TH J3, USB shell stakes, and test points; verify wave fixture and connector insertion access with the assembler. | VERIFY | J1/J3 audits. |

## 3. Placement priority

Placement proceeds in this order. Each priority is a gate for the next one rather than a final coordinate decision.

1. **U1 antenna, board edge and external keepout:** choose the antenna-facing board edge/overhang solution and reserve the complete antenna/enclosure clearance first.
2. **J1 provisional board-edge placement:** locate USB-C at its intended edge but do not freeze the datum until JAE controlled mechanical data is reviewed.
3. **D1 USBLC6-2SC6:** place immediately behind J1, with its connector-side pads facing J1 and chip-side pads facing U1.
4. **USB path and its GND return corridor:** reserve the continuous J1 → D1 → U1 differential-pair corridor before general components consume it.
5. **U2 hot loop:** place U2, its VIN/BST capacitors and GND returns as a compact power-stage cluster, away from the USB/antenna corridor.
6. **L1, output capacitors and FB:** continue the power flow U2 → L1 → C4/C5; put the FB connection at the regulated output and away from SW.
7. **J3 JST PH:** place edge-facing toward the carrier/inter-board gap after confirming cable bend, U1 antenna and enclosure clearance.
8. **Remaining passives, switches, headers and test points:** place only after the critical RF/USB/power/mechanical corridors are protected.

## 4. ESP32-C3 module requirements

| Requirement | Classification | Phase 3B implementation evidence |
| --- | --- | --- |
| Preserve the footprint’s 13.2 × 5.4 mm antenna-area keepout. It forbids tracks, vias, pads, copper pours and footprints on all copper layers. | MUST | Keepout imported/enforced; DRC review. |
| Prefer the on-module antenna outside the base-board edge with feed point near the edge. If not overhanging, place feed near the edge and cut the base board on both sides of/below the antenna; do not use a central four-sided hollow. | MUST | Edge.Cuts and placement review. |
| When the antenna remains over the base board, reserve at least 15 mm clearance in all directions around it inside the product, with no copper, routing or components in the specified clearance. | MUST | PCB/mechanical keepout and enclosure review. |
| Keep USB routing, buck U2/L1/SW copper and return currents, cables and metal/enclosure features outside that antenna clearance. | MUST | Separation review and 3D/mechanical check. |
| Connect EPAD 49 to GND as already netlisted; retain its nine-segment footprint structure. | MUST | Net/footprint review. |
| Choose EPAD stencil aperture and GND-via grid only after fabricator/assembler rules are available. Vias belong in grid gaps; do not invent count or drill. | VERIFY | Stencil/via policy and DRC/manufacturing review. |
| Confirm U1 pin-1, antenna-end and STEP orientation in KiCad GUI/3D viewer. | VERIFY | GUI 3D evidence; unavailable in the isolated environment. |

## 5. USB-C and USB data path

### Fixed net mapping and routing order

```text
J1 A6/B6 D+ → D1.1/6 I/O1 → U1.27 GPIO19 USB_D+
J1 A7/B7 D− → D1.3/4 I/O2 → U1.26 GPIO18 USB_D−
```

Route the pair as **J1 → D1 → U1**, not as a trunk with a D1 branch. Orient D1 so pads 1/3 are on the connector side and pads 6/4 are on the chip side, or use the 180° equivalent that preserves the same through-path.

| Requirement | Classification | Source / review evidence |
| --- | --- | --- |
| Place D1 as close to J1 as practical; no universal distance is fixed. | MUST | ST AN5686 / `usblc6-2sc6-layout-audit.md`. |
| Route D+/D− as a parallel, equal-length differential pair at 90 Ω ±10%. | MUST | Espressif USB layout guidance. |
| Keep a continuous GND reference beneath the pair and ground copper around it. Do not cross plane splits/voids. | MUST | Espressif USB layout guidance. |
| Avoid stubs, arbitrary testpoint tees and unnecessary layer changes. | MUST | ST AN5686 and Espressif USB guidance. |
| If a layer transition is unavoidable, add a nearby pair of GND return vias at each differential transition. | MUST | Espressif USB layout guidance. |
| Derive trace width, pair gap, other-net clearance, allowed length mismatch and via geometry from the selected stack-up; do not copy a geometry from a different stack-up. | VERIFY | Fabricator stack-up / field solver. |
| D1.2 GND must enter a solid GND region through multiple close vias, without a long narrow or thermal-relief path. | MUST | ST DS4260/AN5686. |
| D1.5 VBUS-to-GND decoupling loop must be short; C2 is the existing 100 nF VBUS capacitor. | MUST, subject to conflict below | ST DS4260 Figure 17. |
| Keep `USB_SHIELD` separate from system GND except through the deliberate R3 0 Ω DNP option. D1.2 returns to system GND, not through the shell bond. | MUST | Current schematic / USBLC audit. |
| Keep CC1/CC2 and VBUS outside the controlled pair corridor. | SHOULD | Crosstalk/impedance control; geometry is stack-up-dependent. |
| Keep J1/D1/pair/testpoints as far as possible from the ESP32 antenna and outside its defined clearance. | MUST | Espressif module guidance. |
| Keep USB path/return away from U2 SW node and L1 magnetic region. | MUST | USB, U2 and L1 audits. |

**C2 placement conflict requiring review:** AP63203 layout guidance requires C1/C2 close to U2 VIN/GND, while the USBLC6-2 audit requires C2 close to D1.5/GND as the 100 nF rail-to-rail clamp capacitor. The present schematic has only one C2. During Phase 3B, demonstrate a compact placement that keeps both loops acceptably short. If this cannot be demonstrated, stop and raise a separate schematic-change proposal; this document does not add a capacitor or choose one requirement over the other.

Espressif also recommends reserving 22/33 Ω series and D+/D− shunt-capacitor footprints near the chip. The current schematic has no such options. Do not silently add them in PCB; record a separate approved schematic-change decision if the design team requires the options.

## 6. AP63203 power stage

| Requirement | Classification | Phase 3B evidence |
| --- | --- | --- |
| C1/C2 must be close to U2 VIN/GND, with short, low-inductance input loop and local GND return. | MUST | Component placement and GND-via review. |
| C3 must form the shortest practical BST–SW loop to U2 pins 6/5. | MUST | Routed hot-loop review. |
| Minimize SW copper area and keep SW away from FB, USB pair, antenna clearance and sensitive I2C. | MUST | SW-net copper review. |
| Maintain physical power flow U2 SW → L1 pad 1 → L1 pad 2/+3V3 → C4/C5. Do not reverse L1 pads in the netlist. | MUST | Connectivity and placement review. |
| Place C4/C5 on the post-L1 output with short GND returns; sense FB at the regulated output, away from SW. | MUST | FB trace and output-loop review. |
| Use a continuous GND plane with short capacitor/U2 return and thermal vias. | MUST | GND zone/via review. |
| Verify XGL4020 start-lead/orientation, 3D body, paste/mask and procurement before release. | VERIFY | `xgl4020-footprint-audit.md`. |
| C1/C4/C5 nominal values are retained. Murata official DC-bias representative data is recorded (C1 about 5.28 uF at 5.0 V; C4/C5 about 11.7 uF each / 23.5 uF total at 3.3 V), but worst-condition margin and sufficiency across tolerance, temperature, transient response and stability remain unproven. | VERIFY / release blocker | `murata-mlcc-dc-bias-audit.md`. |
| Test startup, load transient, short-circuit protection and thermal behavior on hardware. | VERIFY / prototype gate | AP63203 audit. |

## 7. JST PH board link

| Requirement | Classification | Evidence |
| --- | --- | --- |
| Keep J3 mapping: 1=3V3, 2=GND, 3=I2C_SDA, 4=I2C_SCL. The cable is 1:1; 1↔4 reversal is prohibited. | MUST | Pad/net and harness review. |
| Use selected side-entry TH `S4B-PH-K-S(LF)(SN)` / C157926 and the accepted 2D KiCad footprint. | MUST | Footprint/pad-1 review. |
| Place main J3 facing the carrier/inter-board gap; preserve lateral cable exit, bend radius, service access, strain relief and antenna clearance. | MUST | Board/mechanical review. |
| Put a clear pin-1 cue and mapping silkscreen where it remains readable after assembly. | MUST | Silkscreen review. |
| Confirm 3D geometry, connector opening direction, CPL rotation, wave fixture, plated-hole/annular-ring convention and order-time eligibility. | VERIFY | `jst-ph-footprint-audit.md`, procurement/assembly review. |
| Confirm carrier J2 uses the same selected part/orientation policy and remains clear of SEN66 airflow/assembly path. | VERIFY | Carrier PCB review. |

## 8. Grounding and return paths

- **MUST:** use a continuous system GND reference plane for U1, USB and power returns; do not introduce a plane split below the USB pair or its ESD return.
- **MUST:** D1.2 must have a short, low-inductance path to the same system GND plane. D1 shell/shield connection is a separate `USB_SHIELD` policy through R3 DNP.
- **MUST:** keep U2 input loop, BST/SW loop and output/FB return compact. Do not route the USB return through a disrupted buck-return region.
- **MUST:** connect U1 EPAD 49 and its GND pins to GND as netlisted; define its via/paste implementation in the board, outside the antenna keepout.
- **MUST:** antenna keepout forbids GND copper, vias and all other copper objects within its 13.2 × 5.4 mm under-module zone. This does not conflict with the requirement for dense GND stitching **near**, but not within, the permitted area around the antenna.
- **VERIFY:** define GND stitching-via pattern, zone priorities and thermal-relief policy after stack-up/fabricator rules are known.

## 9. Routing classes and unresolved stack-up data

Do not freeze these values in KiCad before the selected PCB fabricator provides the actual stack-up and manufacturing rules:

| Data / rule to obtain | Net classes or review it governs |
| --- | --- |
| Layer count/order, dielectric thickness and Er | USB D+/D− 90 Ω differential geometry; reference layer. |
| Copper weight and temperature-rise criteria | +5V, +3V3, buck input/output and GND copper sizing. |
| Minimum track/space, annular ring, drill/finished-hole convention and aspect ratio | USB, U1 EPAD vias, J3 TH pads and all DRC constraints. |
| Impedance calculator/field-solver result | D+/D− width/gap and separation from adjacent copper. |
| Solder-mask, paste and via-tenting/fill rules | U1 EPAD, D1 SOT23-6, L1 and J1/J3 assembly. |
| Controlled-routing and PCBA capability | Differential-pair tolerances, via transitions, CPL rotation and assembly panel/fixture. |

Once supplied, reflect the approved values in KiCad board setup/net classes and re-run DRC; do not infer them from this requirements document.

## 10. Keepouts and separation matrix

| Pair / boundary | Requirement | Status / owner |
| --- | --- | --- |
| Antenna ↔ USB J1/D1/pair/TP | USB features stay outside antenna keepout and as far from antenna as possible; obey external 15 mm rule when applicable. | MUST / Phase 3B |
| Antenna ↔ U2/L1/SW | No switching copper, magnetic component or high-di/dt return in antenna clearance. | MUST / Phase 3B |
| USB pair ↔ SW/L1 | Separate route/returns; no SW crossing/reference disruption. | MUST / Phase 3B |
| FB ↔ SW | FB senses regulated output and is isolated from the noisy SW node. | MUST / Phase 3B |
| J1 ↔ board edge | J1 needs edge/mating envelope, but official JAE datum is still unavailable. | VERIFY / mechanical gate |
| J3 ↔ enclosure/cable | Side-entry cable needs bend, insertion and service/strain-relief clearance. | MUST / Phase 3B, VERIFY 3D |
| U1 EPAD ↔ antenna zone | EPAD GND/vias remain outside the 13.2 × 5.4 mm antenna zone. | MUST / DRC review |
| USB shell ↔ system GND | Maintain separate `USB_SHIELD`; R3 DNP is the deliberate optional bond. | MUST / layout + prototype decision |

## 11. DRC and review checklist

Before declaring a PCB layout review complete, record each item below.

- [ ] No unconnected nets, wrong-net pads or pin-1 orientation errors.
- [ ] Clearance, annular-ring, drill, solder-mask and courtyard DRC pass for chosen manufacturer rules.
- [ ] Edge.Cuts defines the antenna/J1/J3 mechanical intent; no components intrude into title/assembly drawings where relevant.
- [ ] U1 antenna under-module and external clearance are explicit and enforceable; no copper/via/part/route intrusion.
- [ ] U1 EPAD 49 net, paste segmentation and GND-via policy are reviewed.
- [ ] J1 mating edge, shell stakes/NPTH, cable clearance and footprint pin mapping are reviewed against the JAE controlled drawing.
- [ ] D1 is connector-side, C2/D1.5 and D1.2 GND loops are short, and the USB pair has no tee/stub.
- [ ] USB pair obeys stack-up-specific impedance/length rules, has a continuous return plane and has return-via pairs at any layer transition.
- [ ] U2/C1/C2/C3/L1/C4/C5 placement implements compact loops, minimum SW copper and quiet FB routing.
- [ ] J3 and carrier J2 openings, pin-1 cues, 1:1 cable path, bend/strain/airflow clearance and mechanical service access are reviewed.
- [ ] 3D review has checked U1 antenna end, J1/J3 mating, enclosure/mounting/cable collisions and component heights.
- [ ] Silkscreen is readable, does not cover pads, and identifies connector pin 1 / function.

## 12. Manufacturing and assembly VERIFY

| Open evidence | Why release remains blocked |
| --- | --- |
| JAE `SJ121837`, `JACS-30413`, `JAHL-30353-1` | J1 land, board edge, shell/NPTH, mating, paste/mask and assembly cannot yet be accepted. |
| LCSC/JLC stock, Basic/Extended, price, feeder/PCBA eligibility | Live data and PCBA status vary at order time for U1, J1, D1, L1, F1 and J3/J2. |
| CPL rotation / 3D models | J1, J3/J2, U1 and L1 require orientation confirmation. |
| U1 EPAD via/paste rule and MSL 3 handling | Requires assembler/fabricator process agreement. |
| D1 and L1 paste/mask / thermal implementation | Generic or library land pattern must be approved for actual assembly process. |
| C1/C4/C5 DC-bias design margin | Official representative data is obtained; Manufacturing BOM may not freeze before worst-condition effective-capacitance margin is approved or verified by prototype evaluation. |
| Wave/hand-solder fixture and harness | J3/J2 TH connector and cable service policy require fixture/assembly confirmation. |

**BOM freeze, PCBA order and manufacturing release are prohibited until these items are closed.**

## 13. Prototype verification

After DRC/3D/mechanical review and assembly, verify:

- 5 V insertion/startup and 3.3 V regulation under normal and simultaneous ESP32/SEN66 load.
- Buck ripple, load transient, short-circuit/protection response, U2/L1 temperature and PTC voltage drop/heat.
- USB enumeration, CDC/JTAG/download, Full-Speed data stability and cable insertion behavior.
- ESD/EMC behavior appropriate to the product test plan; confirm D1/shield-bond choice with measurement.
- ESP32 Wi-Fi/RF performance in final enclosure, including antenna clearance impact.
- SEN66 I2C communication, sensor power/temperature effects, 40 mm harness handling and airflow impact.

## 14. ACCEPT / VERIFY / CHANGE / BLOCKED summary

| Area | Current classification | Required next action |
| --- | --- | --- |
| Schematic topology | ACCEPT | No schematic change is authorized by this document. |
| U1 2D footprint / mapping | ACCEPT | Implement antenna/EPAD PCB rules and review 3D. |
| D1 pin/net topology | ACCEPT | Implement short J1 → D1 → U1 path and return loop. |
| U2/L1 electrical topology | ACCEPT | Implement hot-loop/FB/GND layout; retain net/pad orientation. |
| J3/J2 part and mapping | ACCEPT | Implement board placement/cable mechanics. |
| C2 shared USBLC/U2 placement | VERIFY | Demonstrate both required loops; escalate a separate schematic-change proposal if impossible. |
| J1 physical footprint/edge data | VERIFY | Obtain and audit controlled JAE documents before freezing edge/assembly. |
| U1 EPAD, antenna external zone, stack-up and PCB mechanics | VERIFY | Define board rules; run DRC/3D review. |
| MLCC effective capacitance | VERIFY | Official Murata representative DC-bias data is obtained; approve worst-condition margin or complete prototype transient/stability evaluation before BOM freeze. No circuit change is currently required. |
| Manufacturing release | CANCELLED | Fabrication is not permitted. The then-open manufacturing, assembly, procurement and prototype checks remain historical, incomplete records. |

## 15. Recommended Phase 3B implementation sequence

1. Create the PCB and enter only preliminary board outline/mechanical constraints; do not freeze dimensions.
2. Place U1 at the selected antenna edge and create its under-module plus external antenna keepouts.
3. Place J1 provisionally at its intended board edge, retaining a visible unresolved JAE mating/edge gate.
4. Place D1 and reserve the uninterrupted J1 → D1 → U1 pair/return corridor; place C2 only after explicitly reviewing the shared-C2 conflict.
5. Place U2/C1/C3/L1/C4/C5 and establish the compact hot-loop, output and FB geometry; coordinate C2 with Step 4.
6. Place J3 facing the carrier gap; reserve cable, bend, strain-relief and service corridors.
7. Establish the GND/zone strategy, including D1 return, U2 thermal return and U1 EPAD policy, without entering antenna keepout.
8. Apply stack-up-derived routing classes; route USB first, then the buck power stage, then remaining signals.
9. Add required GND stitching/return vias and zones; re-check every keepout and reference-plane continuity.
10. Run DRC, inspect silkscreen/courtyard/Edge.Cuts, then carry out GUI 3D/mechanical review.
11. Resolve the open J1, C2, MLCC, procurement and assembly gates before manufacturing data is generated.

## Integrated audit records

- `hardware/pcb/phase-3a-release-review.md`
- `hardware/pcb/jst-ph-header-selection.md`
- `hardware/pcb/jst-ph-footprint-audit.md`
- `hardware/pcb/interconnect-review.md`
- `hardware/pcb/esp32-c3-main/xgl4020-footprint-audit.md`
- `hardware/pcb/esp32-c3-main/usb-c-j1-footprint-audit.md`
- `hardware/pcb/esp32-c3-main/ap63203-pin-audit.md`
- `hardware/pcb/esp32-c3-main/murata-mlcc-dc-bias-audit.md`
- `hardware/pcb/esp32-c3-main/esp32-c3-mini-1-footprint-audit.md`
- `hardware/pcb/esp32-c3-main/usblc6-2sc6-layout-audit.md`
- `hardware/pcb/esp32-c3-main/power-review.md`
- `hardware/pcb/esp32-c3-main/usb-power-review.md`

No commit or push was performed.
